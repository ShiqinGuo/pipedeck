import type { components } from '@/api/schema';

type PipelineJob = components['schemas']['PipelineJob'];

export type JobBucket = 'normal' | 'manual' | 'never' | 'unsupported';

/**
 * 把预览 job 分桶:
 * - normal:按 stage 顺序默认运行
 * - manual:when: manual 单独区
 * - never:when: never 折叠隐藏
 * - unsupported:存在未支持语义的 job 显式阻断
 */
export function bucketizeJobs(jobs: PipelineJob[]): Record<Exclude<JobBucket, 'unsupported'>, PipelineJob[]> {
  const normal: PipelineJob[] = [];
  const manual: PipelineJob[] = [];
  const never: PipelineJob[] = [];
  for (const job of jobs) {
    if (job.when === 'manual') manual.push(job);
    else if (job.when === 'never') never.push(job);
    else normal.push(job);
  }
  return { normal, manual, never };
}

/** job 展示用 image 文本 */
export function jobImageLabel(job: PipelineJob) {
  return job.image?.name ?? '宿主 shell';
}

/** needs 列表展示文本 */
export function jobNeedsLabel(job: PipelineJob) {
  if (!job.needs || job.needs.length === 0) return '—';
  return job.needs.map((need) => (need.optional ? `${need.job}?` : need.job)).join(', ');
}

/** 稳定的 job key */
export function jobKey(job: PipelineJob) {
  return `${job.stage}:${job.name}`;
}
