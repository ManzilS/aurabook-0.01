import type { DemoScenario } from '../types/demo'

function makeEvents(
  id: string,
  left: Array<[number, string, string, number, string]>,
  right: Array<[number, string, string, number, string]>,
) {
  return [
    ...left.map(([at, kind, text, tokenCount, statusLabel], index) => ({
      id: `${id}-left-${index}`,
      at,
      side: 'left' as const,
      kind: kind as DemoScenario['events'][number]['kind'],
      text,
      tokenCount,
      statusLabel,
    })),
    ...right.map(([at, kind, text, tokenCount, statusLabel], index) => ({
      id: `${id}-right-${index}`,
      at,
      side: 'right' as const,
      kind: kind as DemoScenario['events'][number]['kind'],
      text,
      tokenCount,
      statusLabel,
    })),
  ].sort((first, second) => first.at - second.at)
}

export const demoScenarios: DemoScenario[] = [
  {
    id: 'pygame-python-conflict',
    label: 'Python version conflict with pygame',
    title: 'pygame has no wheels for Python 3.13',
    category: 'python',
    prompt: 'Build a simple game with pygame.',
    injectedNote:
      'AuraBook Note: pygame 2.5.x has no prebuilt wheels for Python 3.13+ on Windows — the distutils removal breaks the C extension build. Downgrade to Python 3.12.x and pin pygame==2.5.2 before scaffolding. Alternatively, use pygame-ce which ships 3.13 wheels.',
    leftSummary:
      'The agent hits the missing wheel error, falls back to a source build, fails on the missing distutils/MSVC path, and eventually discovers the Python version mismatch — five turns later.',
    rightSummary:
      'AuraBook injects the known incompatibility before the first install command. The agent selects Python 3.12 and pygame==2.5.2 and scaffolds cleanly.',
    events: makeEvents(
      'pygame',
      [
        [400, 'agent', 'Creating pygame project scaffold and resolving dependencies...', 1640, 'Installing...'],
        [
          1200,
          'error',
          "ERROR: Failed to build 'pygame' when getting requirements to build wheel\nModuleNotFoundError: No module named 'distutils.msvccompiler'",
          3580,
          'Build failed',
        ],
        [
          2400,
          'agent',
          'Retrying with --no-build-isolation and legacy-install-script flags...',
          5340,
          'Retrying source build',
        ],
        [
          3600,
          'error',
          'error: Microsoft Visual C++ 14.0 or greater is required to compile pygame from source.\nSDL2 headers not found on PATH.',
          7210,
          'Compiler missing',
        ],
        [
          5200,
          'agent',
          'Switching to Python 3.12 virtualenv and reinstalling without source flags...',
          9080,
          'Recovering',
        ],
        [6600, 'success', 'pygame 2.5.2 installed via wheel. Project scaffold created.', 10440, 'Complete'],
      ],
      [
        [300, 'agent', 'Scaffolding pygame project...', 1180, 'Booting with community context'],
        [
          900,
          'note',
          'AuraBook Note: pygame 2.5.x has no wheels for Python 3.13+. Use Python 3.12 and pin pygame==2.5.2.',
          1360,
          'Note injected',
        ],
        [1700, 'agent', 'Selecting Python 3.12.x and pinning pygame==2.5.2 before install.', 1790, 'Applying fix'],
        [2900, 'success', 'Wheel found. pygame 2.5.2 installed. Game scaffold ready.', 2380, 'Complete'],
      ],
    ),
  },
  {
    id: 'fastapi-dependency-conflict',
    label: 'Async SQLAlchemy greenlet error in FastAPI',
    title: 'MissingGreenlet kills the FastAPI async stack',
    category: 'backend',
    prompt: 'Set up a FastAPI project with async SQLAlchemy.',
    injectedNote:
      "AuraBook Note: SQLAlchemy 2.x async requires greenlet>=3.0.3 installed alongside asyncpg. Without it, any relationship access raises MissingGreenlet. Pin these together before scaffolding: asyncpg==0.29.0, SQLAlchemy==2.0.25, greenlet==3.0.3, alembic==1.13.1.",
    leftSummary:
      'The agent installs SQLAlchemy and asyncpg separately, hits MissingGreenlet, installs greenlet at the wrong version, then battles an asyncpg connection error before finding the correct pin set.',
    rightSummary:
      'AuraBook surfaces the full pin set before the first install. The async stack bootstraps in one pass.',
    events: makeEvents(
      'fastapi',
      [
        [500, 'agent', 'Generating FastAPI service with async SQLAlchemy and Alembic migrations...', 1820, 'Installing...'],
        [
          1500,
          'error',
          "sqlalchemy.exc.MissingGreenlet: greenlet_spawn has not been called; can't call await_only() here.\nWas IO attempted in an unexpected place? (Background on this error at: https://sqlalche.me/e/20/xd2s)",
          4290,
          'MissingGreenlet',
        ],
        [2800, 'agent', 'Installing greenlet==1.1.3 and re-running migrations...', 6180, 'Patching greenlet'],
        [
          4100,
          'error',
          'asyncpg.exceptions._base.InterfaceError: cannot perform operation: another operation is in progress\nasyncpg 0.27.x is incompatible with SQLAlchemy 2.0.21+ connection pool handshake.',
          8450,
          'asyncpg conflict',
        ],
        [5900, 'agent', 'Upgrading asyncpg and greenlet and pinning versions...', 10220, 'Narrowing pins'],
        [7200, 'success', 'Async stack bootstrapped after finding the correct pin set.', 11780, 'Complete'],
      ],
      [
        [350, 'agent', 'Generating FastAPI async template...', 1240, 'Booting with community context'],
        [
          850,
          'note',
          'AuraBook Note: pin asyncpg==0.29.0, SQLAlchemy==2.0.25, greenlet==3.0.3 together before scaffold.',
          1420,
          'Note injected',
        ],
        [
          1700,
          'agent',
          'Installing SQLAlchemy==2.0.25 + asyncpg==0.29.0 + greenlet==3.0.3 in one pass.',
          1840,
          'Applying fix',
        ],
        [3000, 'success', 'FastAPI async stack created. Alembic migrations ran without errors.', 2490, 'Complete'],
      ],
    ),
  },
  {
    id: 'next-node-mismatch',
    label: 'Node version too old for Prisma 5',
    title: 'Prisma 5 requires Node >=18.18 — Next.js agrees',
    category: 'frontend',
    prompt: 'Create a Next.js app with Prisma.',
    injectedNote:
      'AuraBook Note: Prisma 5 requires Node.js >= 18.18.0 (not just any 18.x). Next.js 14 also requires Node 20+ for stable fetch/streams. Use Node 20.11.1 LTS. Older Node 18.x patch versions trigger Prisma Query Engine binary lookup failures at runtime.',
    leftSummary:
      'The agent creates the app on whatever Node is active, hits a Prisma binary lookup failure, tries downgrading Prisma, then hits a Next.js fetch error before switching to Node 20.',
    rightSummary:
      'AuraBook flags the Node 20 LTS requirement before the first scaffold command. The app boots cleanly.',
    events: makeEvents(
      'next',
      [
        [400, 'agent', 'Running create-next-app with Prisma 5 integration...', 1540, 'Scaffolding...'],
        [
          1300,
          'error',
          "Error: Prisma Client could not locate the Query Engine for runtime 'debian-openssl-3.0.x'.\nExpected in: node_modules/.prisma/client/\nNode.js version: v18.0.0 (Prisma 5 requires >=18.18.0)",
          4120,
          'Engine binary missing',
        ],
        [2700, 'agent', 'Downgrading Prisma to 4.x and regenerating client...', 6040, 'Downgrading Prisma'],
        [
          4200,
          'error',
          "ReferenceError: ReadableStream is not defined\nNext.js 14 relies on the Web Streams API which Node 18.0.x doesn't expose globally.",
          7980,
          'Streams API missing',
        ],
        [5600, 'agent', 'Switching to Node 20.11.1 LTS via nvm and reinstalling...', 9430, 'Recovering'],
        [7000, 'success', 'Next.js 14 + Prisma 5 scaffolded and running on Node 20.11.1 LTS.', 10880, 'Complete'],
      ],
      [
        [250, 'agent', 'Scaffolding Next.js 14 + Prisma 5 baseline...', 1060, 'Booting with community context'],
        [
          750,
          'note',
          'AuraBook Note: Prisma 5 requires Node >=18.18.0; Next.js 14 needs Node 20 LTS for stable fetch. Use Node 20.11.1.',
          1240,
          'Note injected',
        ],
        [
          1550,
          'agent',
          'Selecting Node 20.11.1 LTS via nvm before running create-next-app.',
          1610,
          'Applying fix',
        ],
        [2950, 'success', 'App created and prisma generate ran cleanly on the first pass.', 2290, 'Complete'],
      ],
    ),
  },
  {
    id: 'database-driver-conflict',
    label: 'psycopg2 and asyncpg conflict in async FastAPI',
    title: 'Mixing sync and async Postgres drivers deadlocks the pool',
    category: 'database',
    prompt: 'Provision a service that talks to Postgres over an async driver.',
    injectedNote:
      "AuraBook Note: Never install both psycopg2 and asyncpg in the same async service. psycopg2 is synchronous and will deadlock inside an async event loop — connections become non-awaitable and the pool hangs. Use asyncpg exclusively. Configure SQLAlchemy with create_async_engine('postgresql+asyncpg://...') and remove psycopg2 from requirements entirely.",
    leftSummary:
      'The agent installs psycopg2 for its familiar ORM support, adds asyncpg on top, then battles a non-awaitable connection error and a pool deadlock before removing psycopg2.',
    rightSummary:
      'AuraBook identifies the sync/async driver collision upfront. The service starts on asyncpg only.',
    events: makeEvents(
      'drivers',
      [
        [
          450,
          'agent',
          'Installing psycopg2-binary + asyncpg and configuring SQLAlchemy connection pool...',
          1690,
          'Installing...',
        ],
        [
          1400,
          'error',
          "TypeError: object <psycopg2.extensions.connection object at 0x...> is not awaitable\npsycopg2 connections cannot be used in an async context without a compatibility shim.",
          3940,
          'Not awaitable',
        ],
        [
          2600,
          'agent',
          'Adding aiopg shim layer to wrap psycopg2 connections for async use...',
          5870,
          'Adding shim',
        ],
        [
          3900,
          'error',
          "asyncpg.exceptions._base.InterfaceError: cannot perform operation: another operation is in progress\nThe pool received mixed psycopg2 and asyncpg connection objects.",
          7720,
          'Pool deadlock',
        ],
        [5500, 'agent', 'Removing psycopg2 and shim, standardizing on asyncpg only...', 9100, 'Recovering'],
        [6900, 'success', 'Async Postgres pool stable with asyncpg==0.29.0 only.', 10310, 'Complete'],
      ],
      [
        [280, 'agent', 'Bootstrapping async Postgres service...', 950, 'Booting with community context'],
        [
          700,
          'note',
          'AuraBook Note: do not mix psycopg2 and asyncpg. Use asyncpg only with create_async_engine.',
          1140,
          'Note injected',
        ],
        [
          1500,
          'agent',
          "Installing asyncpg==0.29.0 only. Configuring SQLAlchemy with create_async_engine('postgresql+asyncpg://...').",
          1560,
          'Applying fix',
        ],
        [2750, 'success', 'Async Postgres pool started cleanly. No psycopg2 in the dependency tree.', 2180, 'Complete'],
      ],
    ),
  },
]

export const livePromptSuggestions = [
  'Build a simple game with pygame',
  'Set up a FastAPI project with async SQLAlchemy',
  'Create a Next.js app with Prisma',
]
