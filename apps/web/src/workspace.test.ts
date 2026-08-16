import { describe, expect, it } from 'vitest';

import type { ProjectSummary } from './types';
import { filterProjects, requiredMiddleware } from './workspace';

const projects: ProjectSummary[] = [
  {
    id: 'backend',
    name: 'supplier-backend-v2',
    path: 'D:/code/supplier-backend-v2',
    kind: 'python-uv',
    branch: 'main',
    dirty: false,
    commands: [],
    requirements: ['postgres', 'minio'],
    warnings: [],
    container_capabilities: { dockerfile: 'Dockerfile', compose_files: [] },
  },
  {
    id: 'frontend',
    name: 'supplier-admin-frontend-v2',
    path: 'D:/code/supplier-admin-frontend-v2',
    kind: 'vite-react',
    branch: 'feat/local',
    dirty: true,
    commands: [],
    requirements: [],
    warnings: ['工作区包含未提交修改'],
    container_capabilities: { dockerfile: null, compose_files: [] },
  },
];

describe('workspace selection', () => {
  it('filters by search text and project kind', () => {
    expect(filterProjects(projects, 'supplier', 'vite-react')).toEqual([projects[1]!]);
  });

  it('deduplicates required middleware', () => {
    expect(requiredMiddleware([...projects, projects[0]!])).toEqual(['minio', 'postgres']);
  });
});
