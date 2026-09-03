import { describe, expect, it } from 'vitest';

import type { components } from '@/api/schema';
import { cli } from './cli';
import { bucketizeJobs, jobImageLabel, jobKey, jobNeedsLabel } from './pipeline';
import { RUN_STATUS_LABELS, isDeploymentDegraded, isRunActive } from './status';

type PipelineJob = components['schemas']['PipelineJob'];

function job(overrides: Partial<PipelineJob>): PipelineJob {
  return {
    name: 'build',
    stage: 'build',
    script: ['make build'],
    before_script: [],
    after_script: [],
    image: null,
    variables: {},
    needs: null,
    artifacts: [],
    dotenv_reports: [],
    allow_failure: false,
    when: 'on_success',
    included: true,
    unsupported: [],
    ...overrides,
  };
}

describe('lib/pipeline', () => {
  it('把 when manual 与 never 的 job 分到独立桶,normal 桶保持原顺序', () => {
    const jobs = [
      job({ name: 'deploy-staging', stage: 'deploy', when: 'manual' }),
      job({ name: 'hidden-job', stage: 'cleanup', when: 'never' }),
      job({ name: 'lint', stage: 'test' }),
      job({ name: 'build-app', stage: 'build' }),
    ];
    const buckets = bucketizeJobs(jobs);
    expect(buckets.normal.map((candidate) => candidate.name)).toEqual(['lint', 'build-app']);
    expect(buckets.manual.map((candidate) => candidate.name)).toEqual(['deploy-staging']);
    expect(buckets.never.map((candidate) => candidate.name)).toEqual(['hidden-job']);
  });

  it('job 展示文本区分镜像 job 与宿主 shell job', () => {
    expect(jobImageLabel(job({ image: { name: 'python:3.12', entrypoint: [] } }))).toBe('python:3.12');
    expect(jobImageLabel(job({}))).toBe('宿主 shell');
    expect(jobNeedsLabel(job({ needs: [{ job: 'lint', artifacts: true, optional: false }, { job: 'prepare', artifacts: false, optional: true }] }))).toBe('lint, prepare?');
    expect(jobNeedsLabel(job({}))).toBe('—');
    expect(jobKey(job({ stage: 'test', name: 'lint' }))).toBe('test:lint');
  });
});

describe('lib/status', () => {
  it('运行中状态覆盖 queued 与 running', () => {
    expect(isRunActive('queued')).toBe(true);
    expect(isRunActive('running')).toBe(true);
    expect(isRunActive('succeeded')).toBe(false);
  });

  it('每个 Run 状态都有中文标签', () => {
    expect(Object.keys(RUN_STATUS_LABELS)).toHaveLength(6);
    expect(RUN_STATUS_LABELS.failed).toBe('失败');
  });

  it('degraded 与 failed 的 deployment 需要恢复动作', () => {
    expect(isDeploymentDegraded('degraded')).toBe(true);
    expect(isDeploymentDegraded('failed')).toBe(true);
    expect(isDeploymentDegraded('active')).toBe(false);
    expect(isDeploymentDegraded(null)).toBe(false);
  });
});

describe('lib/cli', () => {
  it('等价命令与 GUI 动作一一对应', () => {
    const repository = { name: 'supplier-backend-v2' } as components['schemas']['RepositoryRecord'];
    expect(cli.run(repository)).toBe('pipedeck run "supplier-backend-v2" --wait');
    expect(cli.reposCheckout(repository, 'release/1.4')).toBe('pipedeck repos checkout "supplier-backend-v2" "release/1.4"');
    expect(cli.reposClone('git@gitlab.example.com:demo.git', 'D:\\code')).toBe('pipedeck repos clone "git@gitlab.example.com:demo.git" --into "D:\\code"');
    expect(cli.envCreate('workspace-1', 'main')).toBe('pipedeck env create "workspace-1" "main"');
    expect(cli.status()).toBe('pipedeck status');
  });
});
