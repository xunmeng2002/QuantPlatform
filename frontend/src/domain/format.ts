/**
 * 时间与数值的展示格式化.
 *
 * **时间戳的时区是这里的关键.** 后端 `clock.utc_now()` 返回的是**朴素 UTC**
 * (`datetime.now(timezone.utc).replace(tzinfo=None)`), 序列化出来是 `2026-09-25T10:00:00`
 * ——既没有 `Z` 也没有偏移量. 而 JS 对**无时区标记的日期时间串**按**本地时间**解释, 于是在
 * UTC+8 上每一个时间戳都会早 8 小时显示 (提交于 18:00 的轮会显示成 10:00). 解析前必须先把
 * `Z` 补回去.
 */

export const ABSENT_PLACEHOLDER = '—';

/** 恰好是「日期 + 时刻」而无时区标记的形状; 带 `Z` 或带偏移量的串不匹配, 也就不动它. */
const NAIVE_UTC_TIMESTAMP_PATTERN =
  /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?$/;

const TRADING_DAY_PATTERN = /^\d{8}$/;

/**
 * 把后端的 UTC 时间串解析成 `Date`.
 *
 * 补 `Z` 这条规则是**唯一**的容错: 解析不了 (空串、杂乱文本) 一律回 `null`, 由格式化函数
 * 显示占位符, 绝不让 `Invalid Date` 流到界面上.
 */
export function parseUtcTimestamp(value: string | null | undefined): Date | null {
  if (typeof value !== 'string') {
    return null;
  }

  const normalized = value.trim();

  if (!normalized) {
    return null;
  }

  const parsed = new Date(
    NAIVE_UTC_TIMESTAMP_PATTERN.test(normalized) ? `${normalized}Z` : normalized,
  );

  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

/** 按**本机时区**显示 `YYYY-MM-DD HH:mm:ss`, 而不是 UTC. */
export function formatDateTime(value: string | null | undefined): string {
  const parsed = parseUtcTimestamp(value);

  if (parsed === null) {
    return ABSENT_PLACEHOLDER;
  }

  const datePart = `${parsed.getFullYear()}-${padTwoDigits(parsed.getMonth() + 1)}-${padTwoDigits(parsed.getDate())}`;
  const timePart = `${padTwoDigits(parsed.getHours())}:${padTwoDigits(parsed.getMinutes())}:${padTwoDigits(parsed.getSeconds())}`;

  return `${datePart} ${timePart}`;
}

/** 交易日是引擎侧的 `YYYYMMDD` 整数串, 这里只加分隔符, 不做时区换算. */
export function formatTradingDay(value: string | null | undefined): string {
  if (typeof value !== 'string') {
    return ABSENT_PLACEHOLDER;
  }

  const trimmed = value.trim();

  if (!trimmed) {
    return ABSENT_PLACEHOLDER;
  }

  if (!TRADING_DAY_PATTERN.test(trimmed)) {
    return trimmed;
  }

  return `${trimmed.slice(0, 4)}-${trimmed.slice(4, 6)}-${trimmed.slice(6, 8)}`;
}

export function formatDuration(durationMs: number | null | undefined): string {
  if (typeof durationMs !== 'number' || !Number.isFinite(durationMs)) {
    return ABSENT_PLACEHOLDER;
  }

  if (durationMs < 1000) {
    return `${Math.round(durationMs)} 毫秒`;
  }

  const totalSeconds = durationMs / 1000;

  if (totalSeconds < 60) {
    return `${totalSeconds.toFixed(1)} 秒`;
  }

  const wholeSeconds = Math.round(totalSeconds);
  const minutes = Math.floor(wholeSeconds / 60);

  return `${minutes} 分 ${wholeSeconds % 60} 秒`;
}

/** 布尔量的三态文案: `null` 是「还没跑到 / 引擎没写」, 与 false 不是一回事. */
export function formatFlag(value: boolean | null | undefined): string {
  if (value === true) {
    return '是';
  }

  if (value === false) {
    return '否';
  }

  return ABSENT_PLACEHOLDER;
}

/**
 * 把后端透传的 JSON 文本缩进后再显示.
 *
 * 解析不了就原样返回: 这些文本是给人看的, 为了一句缩进把整块内容变成一句报错, 只会让人更看不
 * 出来里面是什么.
 */
export function formatJsonText(jsonText: string | null | undefined): string {
  if (typeof jsonText !== 'string' || !jsonText.trim()) {
    return ABSENT_PLACEHOLDER;
  }

  try {
    return JSON.stringify(JSON.parse(jsonText), null, 2);
  } catch {
    return jsonText;
  }
}

/** 金额: 固定两位小数 + 千位分隔. */
export function formatAmount(
  value: number | null | undefined,
  fractionDigits = 2,
): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return ABSENT_PLACEHOLDER;
  }

  const fixedText = Math.abs(value).toFixed(fractionDigits);
  const [integerPart = '0', fractionPart = ''] = fixedText.split('.');
  const grouped = groupThousands(integerPart);
  const magnitude =
    fractionDigits > 0 ? `${grouped}.${fractionPart}` : grouped;

  return value < 0 ? `-${magnitude}` : magnitude;
}

/** 计数: 整数 + 千位分隔. */
export function formatCount(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return ABSENT_PLACEHOLDER;
  }

  return groupThousands(String(Math.trunc(value)));
}

export function formatByteSize(sizeBytes: number | null | undefined): string {
  if (
    typeof sizeBytes !== 'number' ||
    !Number.isFinite(sizeBytes) ||
    sizeBytes < 0
  ) {
    return ABSENT_PLACEHOLDER;
  }

  if (sizeBytes < 1024) {
    return `${sizeBytes} B`;
  }

  const kilobytes = sizeBytes / 1024;

  if (kilobytes < 1024) {
    return `${kilobytes.toFixed(1)} KB`;
  }

  return `${(kilobytes / 1024).toFixed(1)} MB`;
}

/**
 * 千位分隔.
 *
 * 不借 `toLocaleString`: 它按运行环境的 ICU 数据出结果, 不同环境下分组符可能是窄不换行空格,
 * 于是同一条数据在两台机器上长得不一样, 单测也无从断言.
 */
function groupThousands(integerDigits: string): string {
  return integerDigits.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
}

function padTwoDigits(value: number): string {
  return String(value).padStart(2, '0');
}
