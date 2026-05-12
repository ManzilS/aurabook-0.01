"""Synthetic cold-start trajectory generator.

Generates failure→fix trajectories and uploads them to the Cloud Brain.
Supports static cases, random sampling, concurrent generation, and LLM-seeded
new failure case discovery.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import logging
import random
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import Settings, get_settings
from models.schema import TrajectoryIngestRequest, TrajectoryTurn
from services.model_providers import TextGenerationProvider, build_text_generation_provider

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SyntheticFailureCase:
    """One failure trajectory seed."""

    original_goal: str
    original_prompt: str
    broken_code: str
    error_log: str
    tags: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Failure case library — 50 cases across 8 domains
# ---------------------------------------------------------------------------

_CASES: tuple[SyntheticFailureCase, ...] = (
    # ── Python async ────────────────────────────────────────────────────────
    SyntheticFailureCase(
        original_goal="Run a non-blocking delay inside an async Python worker.",
        original_prompt="Fix this async function so it does not block other tasks.",
        broken_code=(
            "import time\n\n"
            "async def poll() -> None:\n"
            "    time.sleep(2)\n"
            "    await fetch_updates()\n"
        ),
        error_log="RuntimeWarning: coroutine latency spike detected due to blocking sleep",
        tags=("python", "async"),
    ),
    SyntheticFailureCase(
        original_goal="Commit rows using SQLAlchemy AsyncSession.",
        original_prompt="Repair this SQLAlchemy async write path.",
        broken_code=(
            "async def create_user(session, payload):\n"
            "    session.add(User(**payload))\n"
            "    session.commit()\n"
        ),
        error_log="RuntimeWarning: coroutine 'AsyncSession.commit' was never awaited",
        tags=("python", "async", "sqlalchemy"),
    ),
    SyntheticFailureCase(
        original_goal="Make HTTP requests inside an async Python service.",
        original_prompt="Replace blocking requests with async-safe httpx calls.",
        broken_code=(
            "import requests\n\n"
            "async def fetch_data(url: str) -> dict:\n"
            "    resp = requests.get(url)\n"
            "    return resp.json()\n"
        ),
        error_log="RuntimeWarning: coroutine blocked event loop via synchronous network I/O",
        tags=("python", "async", "httpx"),
    ),
    SyntheticFailureCase(
        original_goal="Run multiple async tasks and collect all results.",
        original_prompt="Fix this async gather call so it runs tasks concurrently.",
        broken_code=(
            "async def run_all(tasks):\n"
            "    results = []\n"
            "    for t in tasks:\n"
            "        results.append(await t())\n"
            "    return results\n"
        ),
        error_log="Performance: tasks run sequentially instead of concurrently",
        tags=("python", "async"),
    ),
    SyntheticFailureCase(
        original_goal="Close an async context manager properly in a background worker.",
        original_prompt="Fix the async resource leak in this worker loop.",
        broken_code=(
            "async def worker():\n"
            "    client = httpx.AsyncClient()\n"
            "    while True:\n"
            "        data = await client.get('/poll')\n"
            "        process(data.json())\n"
        ),
        error_log="ResourceWarning: Unclosed client session <AsyncClient>",
        tags=("python", "async", "httpx"),
    ),
    SyntheticFailureCase(
        original_goal="Use asyncio.run inside an existing event loop.",
        original_prompt="Fix the nested event loop error in this utility function.",
        broken_code=(
            "def sync_wrapper(coro):\n"
            "    return asyncio.run(coro)\n\n"
            "async def handler():\n"
            "    result = sync_wrapper(fetch())\n"
            "    return result\n"
        ),
        error_log="RuntimeError: This event loop is already running.",
        tags=("python", "async"),
    ),
    # ── FastAPI ─────────────────────────────────────────────────────────────
    SyntheticFailureCase(
        original_goal="Enable CORS for a local FastAPI frontend.",
        original_prompt="Add permissive localhost CORS middleware to this FastAPI app.",
        broken_code=(
            "from fastapi import FastAPI\n\n"
            "app = FastAPI()\n\n"
            "app.add_middleware(\n"
            "    CORSMiddleware,\n"
            "    allow_origins=['http://localhost:3000'],\n"
            "    allow_methods=['*'],\n"
            "    allow_headers=['*'],\n"
            ")\n"
        ),
        error_log="NameError: name 'CORSMiddleware' is not defined",
        tags=("python", "fastapi"),
    ),
    SyntheticFailureCase(
        original_goal="Read JSON from a FastAPI request body safely.",
        original_prompt="Fix this FastAPI handler that crashes on request parsing.",
        broken_code=(
            "from fastapi import FastAPI, Request\n\n"
            "app = FastAPI()\n\n"
            "@app.post('/items')\n"
            "async def create_item(request: Request):\n"
            "    payload = request.json()\n"
            "    return {'name': payload['name']}\n"
        ),
        error_log="TypeError: 'coroutine' object is not subscriptable",
        tags=("python", "fastapi", "async"),
    ),
    SyntheticFailureCase(
        original_goal="Validate a FastAPI request body with Pydantic.",
        original_prompt="Fix the 422 Unprocessable Entity errors on this endpoint.",
        broken_code=(
            "from fastapi import FastAPI\n\n"
            "app = FastAPI()\n\n"
            "@app.post('/users')\n"
            "async def create_user(name: str, age: int):\n"
            "    return {'name': name, 'age': age}\n"
        ),
        error_log="422 Unprocessable Entity: query parameter 'name' missing",
        tags=("python", "fastapi", "pydantic"),
    ),
    SyntheticFailureCase(
        original_goal="Inject a shared database session into FastAPI route handlers.",
        original_prompt="Fix the dependency injection so session is properly scoped.",
        broken_code=(
            "from fastapi import FastAPI\n"
            "from db import SessionLocal\n\n"
            "app = FastAPI()\n\n"
            "@app.get('/users')\n"
            "async def list_users():\n"
            "    db = SessionLocal()\n"
            "    return db.query(User).all()\n"
        ),
        error_log="sqlalchemy.exc.InvalidRequestError: Session is already closed",
        tags=("python", "fastapi", "sqlalchemy"),
    ),
    SyntheticFailureCase(
        original_goal="Return a streaming response from a FastAPI endpoint.",
        original_prompt="Fix this generator endpoint so it actually streams.",
        broken_code=(
            "from fastapi import FastAPI\n\n"
            "app = FastAPI()\n\n"
            "@app.get('/stream')\n"
            "def stream_data():\n"
            "    def gen():\n"
            "        for i in range(10):\n"
            "            yield f'data: {i}\\n'\n"
            "    return gen()\n"
        ),
        error_log="TypeError: Object of type generator is not JSON serializable",
        tags=("python", "fastapi"),
    ),
    SyntheticFailureCase(
        original_goal="Mount a sub-application on a path prefix in FastAPI.",
        original_prompt="Fix the router prefix so sub-routes resolve correctly.",
        broken_code=(
            "from fastapi import FastAPI, APIRouter\n\n"
            "app = FastAPI()\n"
            "router = APIRouter()\n\n"
            "@router.get('/items')\n"
            "async def list_items():\n"
            "    return []\n\n"
            "app.include_router(router)\n"
        ),
        error_log="404 Not Found: GET /api/v1/items",
        tags=("python", "fastapi"),
    ),
    # ── Pydantic ────────────────────────────────────────────────────────────
    SyntheticFailureCase(
        original_goal="Parse a nested JSON payload into a Pydantic v2 model.",
        original_prompt="Fix the Pydantic v2 model so nested objects validate correctly.",
        broken_code=(
            "from pydantic import BaseModel\n\n"
            "class Address(BaseModel):\n"
            "    street: str\n"
            "    city: str\n\n"
            "class User(BaseModel):\n"
            "    name: str\n"
            "    address: dict\n"
        ),
        error_log="ValidationError: 1 validation error for User — address must be Address not dict",
        tags=("python", "pydantic"),
    ),
    SyntheticFailureCase(
        original_goal="Use a Pydantic model as a TypedDict-compatible output.",
        original_prompt="Fix this Pydantic v2 serialization so it returns a plain dict.",
        broken_code=(
            "class Config(BaseModel):\n"
            "    host: str\n"
            "    port: int\n\n"
            "cfg = Config(host='localhost', port=8080)\n"
            "data = dict(cfg)\n"
        ),
        error_log="TypeError: cannot unpack non-sequence Config",
        tags=("python", "pydantic"),
    ),
    SyntheticFailureCase(
        original_goal="Validate environment variables with Pydantic BaseSettings.",
        original_prompt="Fix this settings class so missing env vars raise a clear error.",
        broken_code=(
            "from pydantic import BaseModel\n\n"
            "class Settings(BaseModel):\n"
            "    database_url: str\n"
            "    secret_key: str\n\n"
            "settings = Settings()\n"
        ),
        error_log="ValidationError: 2 validation errors for Settings — database_url field required",
        tags=("python", "pydantic"),
    ),
    # ── Pandas / NumPy ──────────────────────────────────────────────────────
    SyntheticFailureCase(
        original_goal="Normalize prices after filtering active rows in pandas.",
        original_prompt="Fix this pandas transformation so it updates the filtered rows safely.",
        broken_code=(
            "active = df[df['active']]\n"
            "active['price'] = active['price'].fillna(0).astype(float)\n"
        ),
        error_log=(
            "SettingWithCopyWarning: A value is trying to be set on a copy of a slice "
            "from a DataFrame."
        ),
        tags=("python", "pandas"),
    ),
    SyntheticFailureCase(
        original_goal="Merge two DataFrames on a string key column.",
        original_prompt="Fix this merge so it doesn't produce NaNs for all matched rows.",
        broken_code=(
            "result = pd.merge(orders, customers,\n"
            "                  left_on='customer_id',\n"
            "                  right_on='ID')\n"
        ),
        error_log="Result has 0 rows — key dtype mismatch: int64 vs object",
        tags=("python", "pandas"),
    ),
    SyntheticFailureCase(
        original_goal="Compute a rolling average over a time-indexed DataFrame.",
        original_prompt="Fix the rolling window so it operates on the correct time axis.",
        broken_code=(
            "df['rolling_avg'] = df['value'].rolling(7).mean()\n"
        ),
        error_log="ValueError: window must be an integer for non-time-indexed data",
        tags=("python", "pandas"),
    ),
    SyntheticFailureCase(
        original_goal="Apply a vectorised function to a NumPy array without a Python loop.",
        original_prompt="Replace the slow loop with a vectorised NumPy operation.",
        broken_code=(
            "result = []\n"
            "for x in arr:\n"
            "    result.append(x ** 2 + 2 * x + 1)\n"
            "result = np.array(result)\n"
        ),
        error_log="Performance: loop over 10M elements took 12 s; expected < 0.1 s",
        tags=("python", "numpy"),
    ),
    # ── React / TypeScript ──────────────────────────────────────────────────
    SyntheticFailureCase(
        original_goal="Fetch notes when the selected React project changes.",
        original_prompt="Repair this React hook so it doesn't re-render forever.",
        broken_code=(
            "useEffect(() => {\n"
            "  fetch(`/api/notes?project=${projectId}`)\n"
            "    .then((r) => r.json())\n"
            "    .then(setNotes);\n"
            "}, [projectId, notes]);\n"
        ),
        error_log="Warning: Maximum update depth exceeded. This can happen when a component repeatedly calls setState inside useEffect.",
        tags=("react", "typescript"),
    ),
    SyntheticFailureCase(
        original_goal="Share mutable state between sibling React components.",
        original_prompt="Fix the prop-drilling pattern so siblings stay in sync.",
        broken_code=(
            "function Parent() {\n"
            "  const [count, setCount] = useState(0);\n"
            "  return (\n"
            "    <>\n"
            "      <ChildA count={count} />\n"
            "      <ChildB />\n"
            "    </>\n"
            "  );\n"
            "}\n"
        ),
        error_log="ChildB does not receive count; UI out of sync after ChildA increments",
        tags=("react",),
    ),
    SyntheticFailureCase(
        original_goal="Prevent a stale closure inside a React setInterval callback.",
        original_prompt="Fix the stale closure so the callback always reads fresh state.",
        broken_code=(
            "useEffect(() => {\n"
            "  const id = setInterval(() => {\n"
            "    setCount(count + 1);\n"
            "  }, 1000);\n"
            "  return () => clearInterval(id);\n"
            "}, []);\n"
        ),
        error_log="Counter increments only to 1 and then freezes — stale closure over count",
        tags=("react",),
    ),
    SyntheticFailureCase(
        original_goal="Type a React component's props with TypeScript generics.",
        original_prompt="Fix the TypeScript error on this generic list component.",
        broken_code=(
            "function List({ items }) {\n"
            "  return <ul>{items.map(i => <li>{i}</li>)}</ul>;\n"
            "}\n"
        ),
        error_log="TS7006: Parameter 'items' implicitly has an 'any' type.",
        tags=("react", "typescript"),
    ),
    SyntheticFailureCase(
        original_goal="Fetch data from an API in a React Server Component.",
        original_prompt="Fix the async RSC so it doesn't crash during hydration.",
        broken_code=(
            "'use client';\n\n"
            "export default async function Page() {\n"
            "  const data = await fetch('/api/posts').then(r => r.json());\n"
            "  return <ul>{data.map(p => <li key={p.id}>{p.title}</li>)}</ul>;\n"
            "}\n"
        ),
        error_log="Error: async/await is not yet supported in Client Components",
        tags=("react", "typescript", "nextjs"),
    ),
    SyntheticFailureCase(
        original_goal="Memoize a React component to prevent unnecessary re-renders.",
        original_prompt="Fix this component so it only re-renders when props change.",
        broken_code=(
            "const Child = ({ onClick }) => {\n"
            "  console.log('rendered');\n"
            "  return <button onClick={onClick}>click</button>;\n"
            "};\n\n"
            "export default React.memo(Child);\n"
        ),
        error_log="Child still re-renders on every parent update — onClick reference changes each render",
        tags=("react",),
    ),
    # ── Node.js / Express ───────────────────────────────────────────────────
    SyntheticFailureCase(
        original_goal="Handle async errors in an Express route without crashing the server.",
        original_prompt="Wrap the async route so unhandled rejections reach the error middleware.",
        broken_code=(
            "app.get('/users', async (req, res) => {\n"
            "  const users = await db.getAll();\n"
            "  res.json(users);\n"
            "});\n"
        ),
        error_log="UnhandledPromiseRejectionWarning: Unhandled promise rejection",
        tags=("nodejs", "express"),
    ),
    SyntheticFailureCase(
        original_goal="Parse a JSON request body in Express.",
        original_prompt="Fix the Express setup so req.body is populated.",
        broken_code=(
            "const app = express();\n\n"
            "app.post('/data', (req, res) => {\n"
            "  console.log(req.body); // undefined\n"
            "  res.sendStatus(200);\n"
            "});\n"
        ),
        error_log="req.body is undefined — JSON middleware not mounted",
        tags=("nodejs", "express"),
    ),
    SyntheticFailureCase(
        original_goal="Read a file asynchronously without blocking the Node event loop.",
        original_prompt="Replace the sync fs call with the promise-based equivalent.",
        broken_code=(
            "const fs = require('fs');\n\n"
            "app.get('/config', (req, res) => {\n"
            "  const data = fs.readFileSync('./config.json');\n"
            "  res.json(JSON.parse(data));\n"
            "});\n"
        ),
        error_log="Performance: readFileSync blocks event loop for 80ms on each request",
        tags=("nodejs",),
    ),
    # ── SQLAlchemy / databases ───────────────────────────────────────────────
    SyntheticFailureCase(
        original_goal="Perform a bulk insert with SQLAlchemy ORM efficiently.",
        original_prompt="Fix the insert loop to use bulk_insert_mappings instead.",
        broken_code=(
            "for row in data:\n"
            "    session.add(Record(**row))\n"
            "session.commit()\n"
        ),
        error_log="Performance: 10000 individual INSERT statements took 45 s",
        tags=("python", "sqlalchemy"),
    ),
    SyntheticFailureCase(
        original_goal="Query related objects without N+1 database calls.",
        original_prompt="Fix the ORM query to eagerly load the related author.",
        broken_code=(
            "posts = session.query(Post).all()\n"
            "for post in posts:\n"
            "    print(post.author.name)\n"
        ),
        error_log="Performance: 201 SELECT statements for 200 posts — N+1 query detected",
        tags=("python", "sqlalchemy"),
    ),
    SyntheticFailureCase(
        original_goal="Use Alembic to add a nullable column to an existing table.",
        original_prompt="Fix the migration so it doesn't lock the production table.",
        broken_code=(
            "def upgrade():\n"
            "    op.add_column('orders',\n"
            "        sa.Column('notes', sa.Text(), nullable=False))\n"
        ),
        error_log="sqlalchemy.exc.IntegrityError: column 'notes' violates not-null constraint",
        tags=("python", "sqlalchemy", "alembic"),
    ),
    # ── Python typing ───────────────────────────────────────────────────────
    SyntheticFailureCase(
        original_goal="Type a Python function that accepts multiple return types.",
        original_prompt="Fix the return type annotation so mypy is satisfied.",
        broken_code=(
            "def find_user(id: int) -> dict:\n"
            "    if id == 0:\n"
            "        return None\n"
            "    return {'id': id, 'name': 'Alice'}\n"
        ),
        error_log="error: Incompatible return value type (got None, expected dict)  [return-value]",
        tags=("python", "typing"),
    ),
    SyntheticFailureCase(
        original_goal="Use a TypeVar so a generic helper preserves the input type.",
        original_prompt="Fix the TypeVar annotation so the return type matches the argument.",
        broken_code=(
            "from typing import List\n\n"
            "def first(items: List) -> object:\n"
            "    return items[0]\n"
        ),
        error_log="mypy: Return type 'object' loses type information — use TypeVar",
        tags=("python", "typing"),
    ),
    SyntheticFailureCase(
        original_goal="Annotate a dataclass field with a mutable default.",
        original_prompt="Fix the dataclass so it doesn't share the default list across instances.",
        broken_code=(
            "from dataclasses import dataclass\n\n"
            "@dataclass\n"
            "class Task:\n"
            "    tags: list = []\n"
        ),
        error_log="ValueError: mutable default <class 'list'> for field tags is not allowed",
        tags=("python", "typing"),
    ),
    # ── Testing ─────────────────────────────────────────────────────────────
    SyntheticFailureCase(
        original_goal="Parametrize a pytest test over multiple inputs.",
        original_prompt="Fix the parametrize decorator syntax so all cases run.",
        broken_code=(
            "import pytest\n\n"
            "@pytest.mark.parametrize('x, expected', [1, 1], [2, 4])\n"
            "def test_square(x, expected):\n"
            "    assert x ** 2 == expected\n"
        ),
        error_log="TypeError: parametrize() takes 2 positional arguments but 3 were given",
        tags=("python", "pytest"),
    ),
    SyntheticFailureCase(
        original_goal="Mock an external HTTP call inside a pytest test.",
        original_prompt="Fix the mock so it intercepts the actual requests.get call.",
        broken_code=(
            "from unittest.mock import patch\n\n"
            "@patch('requests.get')\n"
            "def test_fetch(mock_get):\n"
            "    mock_get.return_value.json.return_value = {'ok': True}\n"
            "    result = my_module.fetch_data()\n"
            "    assert result == {'ok': True}\n"
        ),
        error_log="AssertionError: mock was not called — patch target does not match import path",
        tags=("python", "pytest"),
    ),
    SyntheticFailureCase(
        original_goal="Write a Jest test for an async React hook.",
        original_prompt="Fix the test so it awaits the async state updates.",
        broken_code=(
            "test('loads data', () => {\n"
            "  const { result } = renderHook(() => useData());\n"
            "  expect(result.current.data).toHaveLength(3);\n"
            "});\n"
        ),
        error_log="Expected array of length 3, received []  — hook has not resolved yet",
        tags=("react", "typescript", "jest"),
    ),
    SyntheticFailureCase(
        original_goal="Use pytest fixtures with async SQLAlchemy sessions.",
        original_prompt="Fix the fixture so each test gets a clean async session.",
        broken_code=(
            "@pytest.fixture\n"
            "def db_session():\n"
            "    session = SessionLocal()\n"
            "    yield session\n"
            "    session.close()\n"
        ),
        error_log="PytestUnraisableExceptionWarning: coroutine 'AsyncSession.close' was never awaited",
        tags=("python", "pytest", "sqlalchemy", "async"),
    ),
    # ── Docker / environment ────────────────────────────────────────────────
    SyntheticFailureCase(
        original_goal="Run a Python service inside Docker with the correct working directory.",
        original_prompt="Fix the Dockerfile so the app module is on the Python path.",
        broken_code=(
            "FROM python:3.12-slim\n"
            "COPY . /app\n"
            "RUN pip install -r requirements.txt\n"
            "CMD [\"python\", \"main.py\"]\n"
        ),
        error_log="ModuleNotFoundError: No module named 'app'",
        tags=("docker",),
    ),
    SyntheticFailureCase(
        original_goal="Pass environment variables into a Docker Compose service.",
        original_prompt="Fix the compose file so DATABASE_URL reaches the app container.",
        broken_code=(
            "services:\n"
            "  api:\n"
            "    build: .\n"
            "    ports:\n"
            "      - '8000:8000'\n"
        ),
        error_log="KeyError: 'DATABASE_URL' — environment variable not injected",
        tags=("docker",),
    ),
    SyntheticFailureCase(
        original_goal="Install a private PyPI package inside Docker without leaking credentials.",
        original_prompt="Fix the Dockerfile to use build secrets for pip authentication.",
        broken_code=(
            "FROM python:3.12-slim\n"
            "ARG PIP_TOKEN\n"
            "ENV PIP_INDEX_URL=https://user:${PIP_TOKEN}@private.pypi/simple\n"
            "RUN pip install my-private-pkg\n"
        ),
        error_log="Security: PIP_TOKEN baked into image layer — visible in docker history",
        tags=("docker",),
    ),
    # ── Django ──────────────────────────────────────────────────────────────
    SyntheticFailureCase(
        original_goal="Add a many-to-many relationship to a Django model.",
        original_prompt="Fix the migration so the join table is created correctly.",
        broken_code=(
            "class Post(models.Model):\n"
            "    tags = models.ManyToManyField('Tag')\n\n"
            "class Tag(models.Model):\n"
            "    name = models.CharField(max_length=50)\n"
        ),
        error_log="django.db.utils.OperationalError: no such table: blog_post_tags",
        tags=("python", "django"),
    ),
    SyntheticFailureCase(
        original_goal="Return a JSON response from a Django view.",
        original_prompt="Fix the view so it serializes the queryset to JSON.",
        broken_code=(
            "from django.http import HttpResponse\n"
            "import json\n\n"
            "def user_list(request):\n"
            "    users = User.objects.all()\n"
            "    return HttpResponse(json.dumps(users))\n"
        ),
        error_log="TypeError: Object of type QuerySet is not JSON serializable",
        tags=("python", "django"),
    ),
    SyntheticFailureCase(
        original_goal="Filter Django ORM results by a related field.",
        original_prompt="Fix the queryset filter to traverse the foreign key correctly.",
        broken_code=(
            "orders = Order.objects.filter(customer.email='alice@example.com')\n"
        ),
        error_log="SyntaxError: expression cannot contain assignment, perhaps you meant '=='?",
        tags=("python", "django"),
    ),
    # ── Imports / modules ───────────────────────────────────────────────────
    SyntheticFailureCase(
        original_goal="Import a local module from a sibling package.",
        original_prompt="Fix the import path so the module resolves at runtime.",
        broken_code=(
            "from utils import helpers\n\n"
            "def process():\n"
            "    return helpers.clean('input')\n"
        ),
        error_log="ModuleNotFoundError: No module named 'utils'",
        tags=("python",),
    ),
    SyntheticFailureCase(
        original_goal="Use a circular import safely in a Python package.",
        original_prompt="Fix the circular import without restructuring the package.",
        broken_code=(
            "# models.py\n"
            "from services import UserService\n\n"
            "# services.py\n"
            "from models import User\n"
        ),
        error_log="ImportError: cannot import name 'User' from partially initialized module 'models'",
        tags=("python",),
    ),
    SyntheticFailureCase(
        original_goal="Conditionally import a heavy optional dependency.",
        original_prompt="Fix the import so the module still works when the dependency is absent.",
        broken_code=(
            "import torch\n\n"
            "def train(data):\n"
            "    model = torch.nn.Linear(10, 1)\n"
            "    return model\n"
        ),
        error_log="ModuleNotFoundError: No module named 'torch'",
        tags=("python",),
    ),
    # ── Configuration / secrets ─────────────────────────────────────────────
    SyntheticFailureCase(
        original_goal="Load secrets from a .env file without committing them to git.",
        original_prompt="Fix the config module to read from environment variables via dotenv.",
        broken_code=(
            "DATABASE_URL = 'postgres://user:password@localhost/db'\n"
            "SECRET_KEY = 'hardcoded-secret'\n"
        ),
        error_log="Security: hardcoded credentials detected in source control",
        tags=("python", "config"),
    ),
    SyntheticFailureCase(
        original_goal="Configure logging so INFO messages appear in production.",
        original_prompt="Fix the logging setup so the root logger level is applied.",
        broken_code=(
            "import logging\n\n"
            "logging.getLogger('myapp').setLevel(logging.DEBUG)\n"
            "logging.info('Server started')\n"
        ),
        error_log="No log output visible — root handler not configured",
        tags=("python",),
    ),
    # ── TypeScript / JavaScript ─────────────────────────────────────────────
    SyntheticFailureCase(
        original_goal="Use optional chaining to safely access a deeply nested object.",
        original_prompt="Fix the null-check so it doesn't throw on missing keys.",
        broken_code=(
            "const city = user.address.city;\n"
        ),
        error_log="TypeError: Cannot read properties of undefined (reading 'city')",
        tags=("typescript",),
    ),
    SyntheticFailureCase(
        original_goal="Import a CommonJS module into an ES module project.",
        original_prompt="Fix the import so the CJS default export is accessible.",
        broken_code=(
            "import lodash from 'lodash';\n"
            "const result = lodash.chunk([1, 2, 3, 4], 2);\n"
        ),
        error_log="SyntaxError: Named export 'chunk' not found. Module 'lodash' is a CommonJS module.",
        tags=("typescript", "nodejs"),
    ),
    SyntheticFailureCase(
        original_goal="Narrow a TypeScript union type inside a conditional block.",
        original_prompt="Fix the type guard so TypeScript narrows the union correctly.",
        broken_code=(
            "function process(input: string | number) {\n"
            "    if (typeof input === 'string') {\n"
            "        return input.toUpperCase();\n"
            "    }\n"
            "    return input.toFixed(2);\n"
            "}\n"
        ),
        error_log="TS2339: Property 'toFixed' does not exist on type 'string | number'.",
        tags=("typescript",),
    ),
    # ── Git / CI ────────────────────────────────────────────────────────────
    SyntheticFailureCase(
        original_goal="Run tests in GitHub Actions for a Python monorepo.",
        original_prompt="Fix the workflow so it installs dependencies before running pytest.",
        broken_code=(
            "jobs:\n"
            "  test:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - uses: actions/checkout@v4\n"
            "      - run: pytest\n"
        ),
        error_log="ModuleNotFoundError: No module named 'pytest'",
        tags=("ci", "github-actions"),
    ),
    SyntheticFailureCase(
        original_goal="Cache pip dependencies in a GitHub Actions workflow.",
        original_prompt="Fix the cache key so it invalidates when requirements.txt changes.",
        broken_code=(
            "- uses: actions/cache@v4\n"
            "  with:\n"
            "    path: ~/.cache/pip\n"
            "    key: pip-cache\n"
        ),
        error_log="Cache always hit even after requirements.txt changed — stale cache served",
        tags=("ci", "github-actions"),
    ),
)


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

_FIX_SYSTEM_PROMPT = (
    "You repair a broken coding attempt. "
    "Return a JSON object with exactly two keys: "
    '"reflection" (one-paragraph root-cause diagnosis) and '
    '"fix" (the corrected, production-ready code snippet with a brief explanation). '
    "Be concise and precise."
)

_LLM_SEED_SYSTEM_PROMPT = (
    "You generate coding failure scenarios for a training dataset. "
    "Return a JSON array of objects. Each object must have exactly these string fields: "
    '"original_goal", "original_prompt", "broken_code", "error_log". '
    "Make each scenario realistic and distinct. "
    "Cover a variety of languages, frameworks, and error classes. "
    "Do not repeat patterns already in the seed list."
)


# ---------------------------------------------------------------------------
# Core generator
# ---------------------------------------------------------------------------


class SwarmGenerator:
    """Generates synthetic trajectories and uploads them to the Cloud Brain."""

    def __init__(
        self,
        settings: Settings,
        *,
        generation_provider: TextGenerationProvider | None = None,
        http_client: httpx.AsyncClient | None = None,
        concurrency: int = 5,
    ) -> None:
        self._settings = settings
        self._provider = generation_provider
        self._http = http_client or httpx.AsyncClient(
            base_url=settings.cloud_api_base_url.rstrip("/"),
            timeout=60.0,
        )
        self._owns_http = http_client is None
        self._sem = asyncio.Semaphore(concurrency)

    async def run(
        self,
        samples: int,
        *,
        extra_cases: list[SyntheticFailureCase] | None = None,
        shuffle: bool = True,
    ) -> list[dict[str, Any]]:
        """Generate and upload *samples* trajectories concurrently."""
        pool = list(_CASES) + (extra_cases or [])
        if shuffle:
            random.shuffle(pool)

        selected: list[SyntheticFailureCase] = []
        while len(selected) < samples:
            if shuffle:
                random.shuffle(pool)
            selected.extend(pool[: samples - len(selected)])

        tasks = [self._process_case(case) for case in selected]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        uploaded: list[dict[str, Any]] = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.warning("Case %d failed: %s", i, result)
            else:
                uploaded.append(result)
        return uploaded

    async def generate_llm_seeds(self, count: int) -> list[SyntheticFailureCase]:
        """Ask the LLM to generate *count* new failure case seeds."""
        provider = self._get_or_build_provider()
        if provider is None:
            logger.warning("No generation provider available — skipping LLM seed generation")
            return []

        existing_goals = "\n".join(f"- {c.original_goal}" for c in _CASES)
        user_prompt = (
            f"Generate {count} new and distinct coding failure scenarios. "
            f"Do not repeat any of these existing goals:\n{existing_goals}\n\n"
            "Return only the JSON array, no markdown."
        )
        try:
            response = await provider.generate_text(
                system_prompt=_LLM_SEED_SYSTEM_PROMPT,
                user_prompt=user_prompt,
            )
            raw = response.text.strip()
            # Strip markdown code fences if present
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            data = json.loads(raw)
            seeds = []
            for item in data:
                try:
                    seeds.append(
                        SyntheticFailureCase(
                            original_goal=item["original_goal"],
                            original_prompt=item["original_prompt"],
                            broken_code=item["broken_code"],
                            error_log=item["error_log"],
                            tags=tuple(item.get("tags", [])),
                        )
                    )
                except (KeyError, TypeError) as exc:
                    logger.warning("Skipping malformed LLM seed: %s", exc)
            logger.info("LLM generated %d new failure seeds", len(seeds))
            return seeds
        except Exception as exc:
            logger.warning("LLM seed generation failed: %s", exc)
            return []

    async def close(self) -> None:
        close = getattr(self._provider, "close", None)
        if callable(close):
            result = close()
            if asyncio.iscoroutine(result):
                await result
        if self._owns_http:
            await self._http.aclose()

    # ── internals ────────────────────────────────────────────────────────────

    async def _process_case(self, case: SyntheticFailureCase) -> dict[str, Any]:
        async with self._sem:
            reflection, final_fix = await self._generate_fix(case)
            payload = self._assemble_payload(case, reflection, final_fix)
            body = json.dumps(payload.model_dump(mode="json"), separators=(",", ":"), sort_keys=True)
            response = await self._http.post(
                "/v1/ingest",
                content=body,
                headers=self._signed_headers(body),
            )
            response.raise_for_status()
            logger.debug("Uploaded trajectory for: %s", case.original_goal[:60])
            return response.json()

    def _signed_headers(self, body: str) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "X-AuraBook-Idempotency-Key": hashlib.sha256(
                f"/v1/ingest:{body}".encode()
            ).hexdigest(),
        }
        if not self._settings.api_shared_secret:
            return headers
        timestamp = str(int(time.time()))
        signature = hmac.new(
            self._settings.api_shared_secret.encode(),
            f"{timestamp}.{body}".encode(),
            hashlib.sha256,
        ).hexdigest()
        headers["X-AuraBook-Timestamp"] = timestamp
        headers["X-AuraBook-Signature"] = signature
        return headers

    def _get_or_build_provider(self) -> TextGenerationProvider | None:
        if self._settings.demo_mode or not self._settings.generation_provider_ready():
            return None
        if self._provider is None:
            self._provider = build_text_generation_provider(self._settings, purpose="fix")
        return self._provider

    async def _generate_fix(self, case: SyntheticFailureCase) -> tuple[str, str]:
        """Return (reflection, final_fix) as separate strings."""
        provider = self._get_or_build_provider()
        if provider is None:
            return self._fallback_fix(case)

        user_prompt = (
            f"Original goal:\n{case.original_goal}\n\n"
            f"Prompt given to the agent:\n{case.original_prompt}\n\n"
            f"Broken code:\n```\n{case.broken_code}\n```\n\n"
            f"Error output:\n{case.error_log}\n\n"
            "Return JSON only — no markdown."
        )
        try:
            response = await provider.generate_text(
                system_prompt=_FIX_SYSTEM_PROMPT,
                user_prompt=user_prompt,
            )
            raw = response.text.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            data = json.loads(raw)
            reflection = data.get("reflection", "").strip()
            final_fix = data.get("fix", "").strip()
            if not reflection or not final_fix:
                raise ValueError("Missing reflection or fix key in LLM response")
            return reflection, final_fix
        except Exception as exc:
            logger.warning("LLM fix generation failed (%s), using fallback", exc)
            return self._fallback_fix(case)

    def _fallback_fix(self, case: SyntheticFailureCase) -> tuple[str, str]:  # noqa: PLR0911, PLR0912
        tags = set(case.tags)
        goal = case.original_goal.lower()
        error = case.error_log.lower()
        code = case.broken_code.lower()

        # ── Python async ────────────────────────────────────────────────────
        if "cors" in goal or "corsmiddleware" in error:
            return (
                "CORSMiddleware was used before being imported, raising a NameError at startup.",
                "Add `from fastapi.middleware.cors import CORSMiddleware` before calling app.add_middleware().",
            )
        if "time.sleep" in code and "async" in goal:
            return (
                "time.sleep() is a blocking call that stalls the entire event loop inside an async function.",
                "Replace `time.sleep(n)` with `await asyncio.sleep(n)` to yield control back to the event loop.",
            )
        if "requests" in code and "httpx" in tags:
            return (
                "The synchronous requests.get() call occupies the thread and blocks every other coroutine in the event loop.",
                "Use `async with httpx.AsyncClient() as client: resp = await client.get(url)` instead.",
            )
        if "run_all" in code or ("gather" in goal and "concurrent" in goal):
            return (
                "Awaiting each coroutine individually in a loop runs them one at a time, not concurrently.",
                "Use `results = await asyncio.gather(*[t() for t in tasks])` to execute all tasks in parallel.",
            )
        if "asyncclient" in code and "resourcewarning" in error:
            return (
                "The AsyncClient is created inside the loop without a context manager, so it is never closed.",
                "Wrap the client in `async with httpx.AsyncClient() as client:` to guarantee cleanup on exit.",
            )
        if "asyncio.run" in code and "already running" in error:
            return (
                "asyncio.run() creates a new event loop, but one is already running in the calling async context.",
                "Replace asyncio.run(coro) with `await coro` directly, or use asyncio.get_event_loop().run_until_complete() from sync code only.",
            )
        # ── SQLAlchemy ───────────────────────────────────────────────────────
        if "sqlalchemy" in tags and "commit" in error:
            return (
                "session.commit() on an AsyncSession returns a coroutine that was never awaited, so the transaction is never flushed.",
                "Change `session.commit()` to `await session.commit()`.",
            )
        if "n+1" in error or "n+1" in goal:
            return (
                "Accessing a lazy-loaded relationship inside a Python loop fires one SELECT per row, producing N+1 database queries.",
                "Add `.options(joinedload(Post.author))` to the query so SQLAlchemy fetches the relationship in a single JOIN.",
            )
        if "bulk" in goal or "individual insert" in error:
            return (
                "Calling session.add() in a loop issues one INSERT per row; for large datasets this is orders of magnitude slower than a bulk operation.",
                "Use `session.bulk_insert_mappings(Record, data)` or the Core `insert()` with executemany to insert all rows in one round-trip.",
            )
        if "alembic" in tags or ("not-null" in error and "column" in error):
            return (
                "Adding a NOT NULL column to an existing table fails because existing rows have no value to satisfy the constraint.",
                "Set `nullable=True` in the migration, backfill existing rows with a default value, then add the NOT NULL constraint in a second migration.",
            )
        if "session" in error and "closed" in error and "fastapi" in tags:
            return (
                "The database session is created directly inside the handler and closed before all ORM operations complete.",
                "Use FastAPI's `Depends` with a generator that yields the session and closes it in a `finally` block: `def get_db(): db = SessionLocal(); try: yield db; finally: db.close()`.",
            )
        # ── FastAPI ──────────────────────────────────────────────────────────
        if "request.json()" in code and "coroutine" in error:
            return (
                "FastAPI's Request.json() is a coroutine; indexing the unawaited coroutine object raises TypeError.",
                "Change `payload = request.json()` to `payload = await request.json()`.",
            )
        if "422" in error and ("name: str, age: int" in code or "query parameter" in error):
            return (
                "Declaring primitive types as route function parameters makes FastAPI treat them as query parameters, not a request body.",
                "Wrap the fields in a Pydantic BaseModel and use it as the parameter type: `async def create_user(data: UserIn): ...`.",
            )
        if "streamingresponse" in goal.replace(" ", "") or "generator is not json" in error:
            return (
                "Returning a bare generator from a route makes FastAPI try to JSON-serialize it, which fails.",
                "Wrap the generator in `StreamingResponse(gen(), media_type='text/event-stream')` and return that instead.",
            )
        if "404" in error and ("include_router" in code or "prefix" in goal):
            return (
                "The router was included without a prefix, so its routes are mounted at the root rather than at `/api/v1`.",
                "Pass the prefix when including: `app.include_router(router, prefix='/api/v1')`.",
            )
        # ── Pydantic ─────────────────────────────────────────────────────────
        if "pydantic" in tags and "dict" in error and "address" in code:
            return (
                "Declaring the field as `dict` bypasses Pydantic's nested model validation; the raw dict is accepted but not coerced.",
                "Change the field type from `dict` to `Address` so Pydantic validates and coerces the nested object automatically.",
            )
        if "cannot unpack non-sequence" in error or ("dict(cfg)" in code):
            return (
                "Pydantic v2 models are not directly unpackable with dict(); the old v1 iteration protocol was removed.",
                "Use `cfg.model_dump()` to convert the model to a plain dict in Pydantic v2.",
            )
        if "basesettings" in goal.replace(" ", "") or ("field required" in error and "settings" in code):
            return (
                "Inheriting from BaseModel instead of BaseSettings means environment variables are never read; all fields must be supplied explicitly.",
                "Change `class Settings(BaseModel)` to `class Settings(BaseSettings)` and import BaseSettings from pydantic_settings.",
            )
        # ── Pandas / NumPy ───────────────────────────────────────────────────
        if "pandas" in tags or "settingwithcopy" in error:
            return (
                "Assigning to a column on a filtered slice creates a copy of the data; the original DataFrame is not modified.",
                "Use `df.loc[mask, 'price'] = ...` to write directly into the owning DataFrame and silence the warning.",
            )
        if "merge" in goal and ("nan" in error or "0 rows" in error or "dtype" in error):
            return (
                "The join keys have different dtypes (e.g. int64 vs object), so no rows match and the result is empty or full of NaNs.",
                "Cast both key columns to the same type before merging: `orders['customer_id'] = orders['customer_id'].astype(str)`.",
            )
        if "rolling" in goal or "rolling window" in error:
            return (
                "Passing an integer window to a non-time-indexed Series uses row-count windows, but the error fires when the index is a DatetimIndex and the window string is missing a frequency.",
                "Either pass an integer (e.g. `rolling(7)`) for row-based windows or set a DatetimeIndex and use an offset string like `rolling('7D')`.",
            )
        if "vectori" in goal or "loop over" in error:
            return (
                "A Python for-loop over a NumPy array interprets each element as a Python object, losing the C-level performance of vectorised operations.",
                "Replace the loop with a NumPy expression: `result = arr ** 2 + 2 * arr + 1` operates on the whole array in one C call.",
            )
        # ── React / TypeScript ───────────────────────────────────────────────
        if "react" in tags and "update depth" in error:
            return (
                "Including the `notes` state variable in the effect dependency array causes the effect to re-run every time notes changes, which triggers another setState, looping forever.",
                "Remove `notes` from the dependency array — only list `projectId`, which is the actual trigger for the fetch.",
            )
        if "sibling" in goal or ("out of sync" in error and "react" in tags):
            return (
                "ChildB never receives `count` because it is not passed as a prop; sibling components cannot share state directly.",
                "Lift the state up to the common Parent and pass `count` as a prop to both ChildA and ChildB.",
            )
        if "stale" in error or "setinterval" in code:
            return (
                "The setInterval callback closes over the initial value of `count` and never sees updates because it was created with an empty dependency array.",
                "Use the functional updater form: `setCount(prev => prev + 1)` so the increment always operates on the latest value.",
            )
        if "ts7006" in error or "implicitly has an 'any'" in error:
            return (
                "Without an explicit type annotation, TypeScript infers the parameter as `any`, which defeats type safety and triggers a strict-mode error.",
                "Add a typed interface: `interface ListProps<T> { items: T[] }` and annotate the component: `function List<T>({ items }: ListProps<T>)`.",
            )
        if "async/await is not yet supported in client" in error or ("'use client'" in code and "async def" in goal):
            return (
                "React Server Components support async/await natively, but Client Components (marked `'use client'`) do not — they run in the browser where top-level await is unavailable.",
                "Remove `'use client'` to make it a Server Component, or move the fetch to a server action and pass the data as props.",
            )
        if "memo" in goal.replace(" ", "") or "onclick reference" in error:
            return (
                "React.memo performs a shallow prop comparison; if `onClick` is a new arrow function on every parent render, the comparison always fails and the child re-renders anyway.",
                "Wrap the callback in `useCallback`: `const handleClick = useCallback(() => { ... }, [])` so the reference is stable across renders.",
            )
        # ── Node.js / Express ────────────────────────────────────────────────
        if "unhandledpromiserejection" in error.replace(" ", "") or ("async" in code and "express" in tags):
            return (
                "async route handlers in Express do not catch rejected promises automatically; an unhandled rejection crashes the process.",
                "Wrap the handler body in try/catch and call `next(err)`, or use a wrapper: `const asyncHandler = fn => (req, res, next) => fn(req, res, next).catch(next)`.",
            )
        if "req.body is undefined" in error or ("express.json" in goal.replace(" ", "") and "undefined" in error):
            return (
                "Express does not parse JSON bodies by default; req.body is undefined until the middleware is registered.",
                "Add `app.use(express.json())` before defining any routes that read `req.body`.",
            )
        if "readfilesync" in code or "readfilesync" in goal.replace(" ", ""):
            return (
                "fs.readFileSync blocks the Node.js event loop for the entire duration of the disk read, stalling all concurrent requests.",
                "Use `const data = await fs.promises.readFile('./config.json', 'utf8')` inside an async handler to keep the event loop free.",
            )
        # ── Testing ──────────────────────────────────────────────────────────
        if "parametrize" in goal.replace(" ", "") or "takes 2 positional" in error:
            return (
                "pytest.mark.parametrize expects a single list of tuples as its second argument; passing multiple positional lists is a syntax error.",
                "Wrap the cases in a list of tuples: `@pytest.mark.parametrize('x, expected', [(1, 1), (2, 4)])`.",
            )
        if "mock" in goal and ("not called" in error or "patch target" in error):
            return (
                "The patch target `requests.get` patches the name in the requests module, but the code under test imported `get` directly, so the original function is called.",
                "Patch the name as it is looked up in the module under test: `@patch('my_module.requests.get')` or `@patch('my_module.fetch_data')`.",
            )
        if "jest" in tags or ("async" in goal and "hook" in goal and "haslength" in error.replace(" ", "")):
            return (
                "The assertion runs synchronously before the async hook has resolved, so `data` is still the initial empty array.",
                "Wrap the assertion in `await waitFor(() => expect(result.current.data).toHaveLength(3))` to wait for the state update.",
            )
        if "pytest-asyncio" in goal.replace("-", "") or ("asyncsession" in goal.replace(" ", "") and "pytest" in tags):
            return (
                "A sync pytest fixture cannot yield an async resource; the coroutine is never awaited and the session is never properly opened.",
                "Mark the fixture `@pytest_asyncio.fixture` and make it `async def`, then use `async with AsyncSession(engine) as session: yield session`.",
            )
        # ── Docker / environment ─────────────────────────────────────────────
        if "docker" in tags and "module" in error:
            return (
                "No WORKDIR is set in the Dockerfile, so Python starts from the filesystem root and cannot resolve the app package.",
                "Add `WORKDIR /app` after the COPY instruction so Python's working directory matches where the package lives.",
            )
        if "database_url" in error or ("environment variable" in error and "docker" in tags):
            return (
                "The service definition has no `environment` or `env_file` key, so Docker Compose does not inject any variables into the container.",
                "Add an `environment:` block under the service: `environment: - DATABASE_URL=${DATABASE_URL}` or use `env_file: .env`.",
            )
        if "pip_token" in code or "baked into image" in error:
            return (
                "ARG values are stored in the image layer history, so the token is readable by anyone with access to the image.",
                "Use Docker BuildKit secrets: `RUN --mount=type=secret,id=pip_token pip install --index-url https://user:$(cat /run/secrets/pip_token)@private.pypi/simple my-pkg`.",
            )
        # ── Django ───────────────────────────────────────────────────────────
        if "django" in tags and "no such table" in error:
            return (
                "Defining a ManyToManyField creates the schema only after `makemigrations` and `migrate` are run; the join table does not exist yet.",
                "Run `python manage.py makemigrations && python manage.py migrate` to create the join table in the database.",
            )
        if "queryset is not json serializable" in error or ("django" in tags and "httpresponse" in code):
            return (
                "json.dumps cannot serialize a QuerySet object; it only handles Python primitives.",
                "Use Django's `JsonResponse` with `list(queryset.values())` or a serializer: `from django.http import JsonResponse; return JsonResponse(list(qs.values()), safe=False)`.",
            )
        if "expression cannot contain assignment" in error and "django" in tags:
            return (
                "Django ORM filter kwargs use double-underscore traversal; a dot notation is a Python syntax error.",
                "Use keyword argument syntax with double underscores: `Order.objects.filter(customer__email='alice@example.com')`.",
            )
        # ── Imports / modules ────────────────────────────────────────────────
        if "no module named 'utils'" in error or ("sibling package" in goal and "modulenotfound" in error):
            return (
                "The module is resolved relative to the working directory at runtime; if the script is run from a different directory the import fails.",
                "Add the project root to sys.path or run the module with `python -m package.module` so Python uses the package's `__init__.py` for resolution.",
            )
        if "partially initialized module" in error or "circular import" in goal.replace(" ", ""):
            return (
                "Python begins executing `models.py`, then hits `from services import UserService` which triggers `services.py` to execute, which in turn tries to import from the not-yet-finished `models.py`.",
                "Break the cycle by moving the import inside the function body: `def get_user(): from models import User; return User`.",
            )
        if "no module named 'torch'" in error or "optional dependency" in goal:
            return (
                "A top-level import of an optional dependency raises ModuleNotFoundError for users who have not installed it.",
                "Guard the import: `try: import torch; HAS_TORCH = True; except ImportError: HAS_TORCH = False` and raise a clear error inside the function if the dependency is absent.",
            )
        # ── Config / secrets ─────────────────────────────────────────────────
        if "hardcoded credentials" in error or ("secrets" in goal and "dotenv" in goal.replace(" ", "")):
            return (
                "Hardcoding credentials in source files exposes them in version control and every environment that runs the code.",
                "Move secrets to a `.env` file, add it to `.gitignore`, and read them with `python-dotenv`: `from dotenv import load_dotenv; load_dotenv(); os.environ['SECRET_KEY']`.",
            )
        if "root handler not configured" in error or "no log output" in error:
            return (
                "Setting a level on a named logger has no effect until a handler is attached; the root logger's default level is WARNING so INFO messages are suppressed.",
                "Call `logging.basicConfig(level=logging.INFO)` once at program startup before any log calls.",
            )
        # ── TypeScript ────────────────────────────────────────────────────────
        if "cannot read properties of undefined" in error or ("optional chaining" in goal):
            return (
                "Accessing `.city` on `user.address` raises TypeError when `address` is undefined; there is no null check.",
                "Use optional chaining: `const city = user?.address?.city` to safely return undefined instead of throwing.",
            )
        if "commonjs module" in error or ("esm" in goal and "cjs" in goal.replace(" ", "")):
            return (
                "Lodash is a CommonJS module; ES module named imports do not work with it directly.",
                "Use a default import and destructure: `import _ from 'lodash'; const result = _.chunk([1,2,3,4], 2)` or switch to lodash-es.",
            )
        if "ts2339" in error or ("union type" in goal and "toFixed" in case.broken_code):
            return (
                "TypeScript cannot call `.toFixed()` on a `string | number` union because `string` does not have that method; the narrowing in the else branch is lost.",
                "The narrowing is actually correct as written — TypeScript should accept it. Ensure you are not widening the type elsewhere, or add an explicit `else if (typeof input === 'number')` branch.",
            )
        # ── CI / GitHub Actions ───────────────────────────────────────────────
        if "github-actions" in tags and "no module named 'pytest'" in error:
            return (
                "The workflow checks out the code and immediately runs pytest without setting up Python or installing dependencies.",
                "Add `uses: actions/setup-python@v5` with a `python-version` and a `run: pip install -r requirements.txt` step before the pytest step.",
            )
        if "github-actions" in tags and "stale cache" in error:
            return (
                "Using a fixed string as the cache key means the cache never invalidates, even when requirements.txt changes.",
                "Include a hash of the requirements file in the key: `key: pip-${{ hashFiles('requirements.txt') }}`.",
            )

        # ── Final fallback (should only fire for genuinely new cases) ─────────
        tag_str = ", ".join(sorted(tags)) or "general"
        return (
            f"The error `{case.error_log[:120]}` was triggered by an incorrect usage pattern in a {tag_str} context.",
            "Apply the minimal corrective change that removes the root cause shown in the error log, preferring the framework-native or async-safe API.",
        )

    def _assemble_payload(
        self,
        case: SyntheticFailureCase,
        reflection: str,
        final_fix: str,
    ) -> TrajectoryIngestRequest:
        turn = TrajectoryTurn(
            prompt=(
                f"{case.original_prompt}\n\n"
                f"Broken code:\n```\n{case.broken_code}\n```\n\n"
                f"Error:\n{case.error_log}"
            ),
            response=final_fix,
            thought=reflection,
            outcome="success",
        )
        return TrajectoryIngestRequest(
            source="swarm",
            original_goal=case.original_goal,
            original_prompt=case.original_prompt,
            broken_code=case.broken_code,
            error_log=case.error_log,
            ai_reflection=reflection,
            final_fix=final_fix,
            history=[turn],
            metadata={
                "synthetic": True,
                "tags": list(case.tags),
            },
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


async def _main_async(args: argparse.Namespace) -> None:
    settings = get_settings()
    generator = SwarmGenerator(settings, concurrency=args.concurrency)
    try:
        extra_cases: list[SyntheticFailureCase] = []
        if args.llm_seeds > 0:
            logger.info("Requesting %d LLM-generated failure seeds…", args.llm_seeds)
            extra_cases = await generator.generate_llm_seeds(args.llm_seeds)

        uploaded = await generator.run(
            args.samples,
            extra_cases=extra_cases,
            shuffle=not args.no_shuffle,
        )
        logger.info("Uploaded %d synthetic trajectories", len(uploaded))
        for result in uploaded:
            logger.debug("  → %s", result)
    finally:
        await generator.close()


def main() -> None:
    """CLI entry point for the synthetic swarm generator."""
    parser = argparse.ArgumentParser(
        description="Generate synthetic AuraBook trajectories and upload them to the Cloud Brain."
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=20,
        help="Number of synthetic trajectories to upload (default: 20).",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=5,
        help="Max parallel generation + upload requests (default: 5).",
    )
    parser.add_argument(
        "--llm-seeds",
        type=int,
        default=0,
        metavar="N",
        help="Ask the LLM to generate N additional failure cases before sampling (default: 0).",
    )
    parser.add_argument(
        "--no-shuffle",
        action="store_true",
        help="Cycle through cases in order instead of randomly sampling.",
    )
    args = parser.parse_args()

    settings = get_settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    asyncio.run(_main_async(args))


if __name__ == "__main__":
    main()
