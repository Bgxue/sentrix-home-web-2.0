/**
 * 源文件卫生门禁。
 *
 * **为什么要有这条。** 1.0 `src/people_graph.js:50` 用 `"\u0000"` 当无向 pair key 的
 * 分隔符，移植到 2.0 时被写成了**裸 NUL 字节**（0x00），前后出现过三次
 * （`graphLayout.ts` 两次、`useFamilyGraph.ts` 一次）。
 *
 * 它不会让程序崩——JS 字符串里放 NUL 完全合法——真正的代价是**评审盲区**：
 * `git` 把这个文件判成二进制，于是 `git diff`、`git log -p`、PR 的文件视图
 * **一个字都不显示**，只有 `Bin 7999 -> 12140 bytes` 这一行。一个几百行的源文件
 * 从此在代码评审里隐身。而且编辑器与 `Read` 工具会把它渲染成空格，人眼也发现不了。
 *
 * 逐次修没用（已经复发两次），所以订成规则让它在门禁上现形。
 */

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

/** 源码根。`vitest` 的工作目录就是 `apps/web`（见 `vite.config.ts` 与 Makefile）。 */
const SRC = join(process.cwd(), "src");

const SKIP = new Set(["node_modules", "dist"]);

function sourceFiles(dir: string): string[] {
  const found: string[] = [];
  for (const entry of readdirSync(dir)) {
    if (SKIP.has(entry)) continue;
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) found.push(...sourceFiles(path));
    else found.push(path);
  }
  return found;
}

describe("源文件卫生", () => {
  it("源码里没有裸 NUL 字节（它会让 git 把文件判成二进制，diff 里完全隐身）", () => {
    const offenders = sourceFiles(SRC)
      .filter((path) => readFileSync(path).includes(0))
      // 绝对路径在报错里太长，只留 src 之后那段。
      .map((path) => path.slice(SRC.length + 1));

    expect(
      offenders,
      "这些文件里有 0x00 字节。分隔符请写成 \\u0000 转义，不要写字面 NUL。",
    ).toEqual([]);
  });
});
