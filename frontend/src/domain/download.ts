/**
 * 产物下载的两件纯逻辑: 路径怎么进 URL, 字节怎么落到用户磁盘.
 *
 * 下载不能走 `window.open`: 那是一次**不带 Authorization 头**的新导航, 后端会回 401. 必须先用
 * `fetch` 取回字节 (带上令牌), 再在本地触发一次保存.
 */

/**
 * 把产物的相对路径编码进 URL.
 *
 * 逐段编码而不是整串 `encodeURIComponent`: 路由是 `/{file_path:path}`, 段间的 `/` 必须原样
 * 留着, 整串编码会把它变成 `%2F` 而匹配不到路径参数. 段内的空格、`#`、`?`、中文仍要编码.
 */
export function encodeArtifactPath(relativePath: string): string {
  return relativePath
    .split('/')
    .map((pathSegment) => encodeURIComponent(pathSegment))
    .join('/');
}

/** 保存时用的文件名: 取路径的最后一段 (`Dump/<RunId>/t_trade.csv` → `t_trade.csv`). */
export function artifactFilename(relativePath: string): string {
  const pathSegments = relativePath.split('/').filter((segment) => segment !== '');

  return pathSegments.length > 0 ? pathSegments[pathSegments.length - 1] : '';
}

/** 触发一次浏览器保存. 仅在浏览器里可调, 故不进单测 (单测跑在 node 环境). */
export function saveBlobAsFile(blob: Blob, filename: string): void {
  const objectUrl = URL.createObjectURL(blob);
  const downloadAnchor = document.createElement('a');

  downloadAnchor.href = objectUrl;
  downloadAnchor.download = filename;
  downloadAnchor.rel = 'noopener';
  downloadAnchor.click();

  // 立刻 revoke 会让部分浏览器取消刚开始的下载, 故推到下一个任务再回收这条 URL.
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
}
