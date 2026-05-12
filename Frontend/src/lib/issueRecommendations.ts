import type { ErrorCluster, KnowledgeNote } from '../types/dashboard'

export interface IssueRecommendation {
  title: string
  detail: string
}

function mentionsPermissionIssue(text: string): boolean {
  const lowered = text.toLowerCase()
  return lowered.includes('permission denied') || lowered.includes('access is denied') || lowered.includes('site-packages')
}

export function recommendationsForNote(
  note: KnowledgeNote,
  cluster: ErrorCluster | null,
): IssueRecommendation[] {
  const items: IssueRecommendation[] = [
    {
      title: 'Use the saved diagnosis first',
      detail: 'Start from the captured diagnosis summary before trying new experiments so the next model does not repeat the same blind first step.',
    },
    {
      title: 'Reproduce with the recorded environment',
      detail: `Match ${note.environment.languageVersion} and the recorded libraries before trusting any new repair attempt.`,
    },
  ]

  if (note.solutionText.trim()) {
    items.push({
      title: 'Apply the known successful fix path',
      detail: 'The issue already has a successful community fix. Try that route before exploring lower-confidence alternatives.',
    })
  }

  if (note.attemptCount > 0) {
    items.push({
      title: 'Compare against prior attempts',
      detail: `There are ${note.attemptCount} captured attempts for this issue. Review the tried/outcome pairs before rerunning another model.`,
    })
  }

  if (cluster && cluster.cascadeErrorCount > 0) {
    items.push({
      title: 'Inspect the follow-on error chain',
      detail: `This issue generated ${cluster.cascadeErrorCount} follow-on errors. The full error page will show where the repair path branched.`,
    })
  }

  if (mentionsPermissionIssue(`${note.problemDescription}\n${note.solutionText}\n${note.fullDiagnosis}`)) {
    items.push({
      title: 'Check for environment or permission blockers',
      detail: 'This looks like an environment-level failure. Verify writable virtualenvs, runtime permissions, and global install paths before changing repo code again.',
    })
  }

  return items.slice(0, 5)
}

export function recommendationsForErrorCluster(
  cluster: ErrorCluster,
  note: KnowledgeNote | null,
): IssueRecommendation[] {
  const items: IssueRecommendation[] = [
    {
      title: 'Start at the root error',
      detail: 'Trace the chain from the first failure instead of focusing only on the latest returned error. Many later failures are side effects of an earlier bad repair.',
    },
    {
      title: 'Avoid repeating failed repair paths',
      detail: `This cluster already contains ${cluster.attemptCount} attempts and ${cluster.failureCount} failure states. Reuse the chain to skip repeated dead ends.`,
    },
  ]

  if (cluster.attemptedModels.length > 0) {
    items.push({
      title: 'Compare models before escalating',
      detail: `Models already tried: ${cluster.attemptedModels.join(', ')}. Use that history to decide whether a different model is likely to add signal.`,
    })
  }

  if (cluster.cascadeErrorCount > 0) {
    items.push({
      title: 'Watch the branch points',
      detail: `There are ${cluster.cascadeErrorCount} returned-error nodes. Those are the moments where one attempted fix created a new failure branch.`,
    })
  }

  if (note && note.solutionText.trim()) {
    items.push({
      title: 'Cross-check the linked note',
      detail: 'This error cluster is connected to a stored note and solution path. Compare the root cause against that note before trying a fresh fix.',
    })
  } else if (cluster.unresolved) {
    items.push({
      title: 'Prioritize manual triage',
      detail: 'This issue is still unresolved. Export the full chain, inspect the environment assumptions, and submit a manual solution once the root cause is confirmed.',
    })
  }

  if (mentionsPermissionIssue(`${cluster.rootErrorDetail}\n${cluster.fullDiagnosis}`)) {
    items.push({
      title: 'Treat this as infrastructure-first',
      detail: 'The chain suggests a permissions or environment issue. Fix runtime isolation or install paths before editing application code.',
    })
  }

  return items.slice(0, 5)
}
