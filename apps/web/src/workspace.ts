import type { MiddlewareKind, ProjectKind, ProjectSummary } from './types';

export type ProjectFilter = ProjectKind | 'all';

export function filterProjects(
  projects: ProjectSummary[],
  query: string,
  kind: ProjectFilter,
): ProjectSummary[] {
  const normalized = query.trim().toLocaleLowerCase();
  return projects.filter((project) => {
    const matchesKind = kind === 'all' || project.kind === kind;
    const searchable = `${project.name} ${project.path} ${project.branch}`.toLocaleLowerCase();
    return matchesKind && (normalized.length === 0 || searchable.includes(normalized));
  });
}
export function requiredMiddleware(projects: ProjectSummary[]): MiddlewareKind[] {
  const kinds = new Set(projects.flatMap((project) => project.requirements));
  return [...kinds].sort();
}
