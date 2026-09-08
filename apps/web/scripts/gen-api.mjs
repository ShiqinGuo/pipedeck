// 从 Python 控制服务导出 OpenAPI,并用 openapi-typescript 生成前端类型。
// 用法:corepack pnpm --filter @pipedeck/web gen:api
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptDir = dirname(fileURLToPath(import.meta.url));
const webDir = dirname(scriptDir);
const repoRoot = dirname(dirname(webDir));
const openapiPath = join(webDir, 'openapi.json');

// 1. 通过临时 py 文件调用 uv 运行 FastAPI app 导出 openapi.json,
//    避免在 Windows shell 上传递含空格与分号的 -c 参数。
const tmpDir = mkdtempSync(join(webDir, '.gen-'));
const dumpScript = join(tmpDir, 'dump_openapi.py');
writeFileSync(dumpScript, 'import json\nfrom pipedeck.api import app\nprint(json.dumps(app.openapi()))\n');
try {
  const dump = spawnSync('uv', ['run', 'python', dumpScript], {
    cwd: repoRoot,
    env: { ...process.env, PIPEDECK_STATE_DB_PATH: join(tmpDir, 'state.db') },
    encoding: 'utf8',
    shell: process.platform === 'win32',
  });
  if (dump.status !== 0) {
    console.error(dump.stderr);
    throw new Error('导出 OpenAPI 失败：请确认 uv 环境可用');
  }
  // 校验是合法 JSON 再落盘
  const schema = JSON.stringify(JSON.parse(dump.stdout), null, 2);
  writeFileSync(openapiPath, `${schema}\n`);
} catch (error) {
  if (!(error instanceof Error && error.message.startsWith('导出 OpenAPI 失败'))) {
    console.error('OpenAPI 导出结果不是合法 JSON');
  }
  throw error;
} finally {
  rmSync(tmpDir, { recursive: true, force: true });
}

// 2. openapi-typescript 生成 src/api/schema.d.ts(枚举生成为 string union,便于与字面量比较)
const result = spawnSync(
  'corepack',
  ['pnpm', 'exec', 'openapi-typescript', openapiPath, '-o', join(webDir, 'src', 'api', 'schema.d.ts')],
  { cwd: webDir, encoding: 'utf8', shell: process.platform === 'win32', stdio: 'inherit' },
);
if (result.status !== 0) throw new Error('openapi-typescript 生成失败');
console.log(`已生成 ${openapiPath} 与 src/api/schema.d.ts`);
console.log(`${readFileSync(join(webDir, 'src', 'api', 'schema.d.ts'), 'utf8').split('\n').length} 行类型`);
