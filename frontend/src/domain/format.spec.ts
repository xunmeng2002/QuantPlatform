/**
 * 展示格式化的纯逻辑.
 *
 * 最要紧的一条是**时区**: 后端 `clock.utc_now()` 给的是无时区标记的朴素 UTC, 而 JS 会把这种串
 * 按本地时间解释. 下面第一个用例就是这条的回归守卫 —— 去掉补 `Z` 那一步, 在 UTC+8 上它会转红
 * (在 UTC 环境里两种写法恰好同值, 这是时区带来的固有盲区, 无法用断言消掉).
 */

import { describe, expect, it } from 'vitest';

import {
  ABSENT_PLACEHOLDER,
  formatAmount,
  formatByteSize,
  formatCount,
  formatDateTime,
  formatDuration,
  formatFlag,
  formatJsonText,
  formatTradingDay,
  parseUtcTimestamp,
} from './format';

const SAMPLE_UTC_MILLIS = Date.UTC(2026, 8, 25, 10, 0, 0);

function padTwoDigits(value: number): string {
  return String(value).padStart(2, '0');
}

describe('parseUtcTimestamp', () => {
  it('无时区标记的串按 UTC 解释, 不是本地时间', () => {
    expect(parseUtcTimestamp('2026-09-25T10:00:00')?.getTime()).toBe(SAMPLE_UTC_MILLIS);
    expect(parseUtcTimestamp('2026-09-25 10:00:00.500')?.getTime()).toBe(
      SAMPLE_UTC_MILLIS + 500,
    );
  });

  it('已带时区标记的串不被二次改写', () => {
    expect(parseUtcTimestamp('2026-09-25T10:00:00Z')?.getTime()).toBe(SAMPLE_UTC_MILLIS);
    expect(parseUtcTimestamp('2026-09-25T18:00:00+08:00')?.getTime()).toBe(SAMPLE_UTC_MILLIS);
  });

  it('解析不了的一律回 null, 不让 Invalid Date 流到界面上', () => {
    expect(parseUtcTimestamp('')).toBeNull();
    expect(parseUtcTimestamp('   ')).toBeNull();
    expect(parseUtcTimestamp('昨天')).toBeNull();
    expect(parseUtcTimestamp(null)).toBeNull();
    expect(parseUtcTimestamp(undefined)).toBeNull();
  });
});

describe('formatDateTime', () => {
  it('按本机时区显示 UTC 时刻对应的本地时间', () => {
    const localInstant = new Date(SAMPLE_UTC_MILLIS);
    const expected = [
      `${localInstant.getFullYear()}-${padTwoDigits(localInstant.getMonth() + 1)}-${padTwoDigits(localInstant.getDate())}`,
      `${padTwoDigits(localInstant.getHours())}:${padTwoDigits(localInstant.getMinutes())}:${padTwoDigits(localInstant.getSeconds())}`,
    ].join(' ');

    expect(formatDateTime('2026-09-25T10:00:00')).toBe(expected);
    expect(formatDateTime('2026-09-25T10:00:00Z')).toBe(expected);
  });

  it('拿不到时间就显示占位符', () => {
    expect(formatDateTime(null)).toBe(ABSENT_PLACEHOLDER);
    expect(formatDateTime('')).toBe(ABSENT_PLACEHOLDER);
  });
});

describe('formatTradingDay', () => {
  it('8 位数字串加分隔符, 其余原样', () => {
    expect(formatTradingDay('20240102')).toBe('2024-01-02');
    expect(formatTradingDay('2024-01-02')).toBe('2024-01-02');
    expect(formatTradingDay('')).toBe(ABSENT_PLACEHOLDER);
    expect(formatTradingDay(null)).toBe(ABSENT_PLACEHOLDER);
  });
});

describe('formatDuration', () => {
  it('毫秒 / 秒 / 分秒三档', () => {
    expect(formatDuration(0)).toBe('0 毫秒');
    expect(formatDuration(999)).toBe('999 毫秒');
    expect(formatDuration(1500)).toBe('1.5 秒');
    expect(formatDuration(90000)).toBe('1 分 30 秒');
    expect(formatDuration(null)).toBe(ABSENT_PLACEHOLDER);
  });
});

describe('formatAmount 与 formatCount', () => {
  it('金额两位小数加千位分隔, 负号在最前', () => {
    expect(formatAmount(1234567.891)).toBe('1,234,567.89');
    expect(formatAmount(-1234.5)).toBe('-1,234.50');
    expect(formatAmount(0)).toBe('0.00');
    expect(formatAmount(null)).toBe(ABSENT_PLACEHOLDER);
  });

  it('计数取整后加千位分隔', () => {
    expect(formatCount(2928)).toBe('2,928');
    expect(formatCount(84)).toBe('84');
    expect(formatCount(-1234)).toBe('-1,234');
    expect(formatCount(null)).toBe(ABSENT_PLACEHOLDER);
  });

  it('不用 toLocaleString: 分组符与环境无关', () => {
    expect(formatAmount(1234567.891)).toContain(',');
  });
});

describe('formatByteSize', () => {
  it('B / KB / MB 三档', () => {
    expect(formatByteSize(512)).toBe('512 B');
    expect(formatByteSize(2048)).toBe('2.0 KB');
    expect(formatByteSize(1572864)).toBe('1.5 MB');
    expect(formatByteSize(-1)).toBe(ABSENT_PLACEHOLDER);
    expect(formatByteSize(null)).toBe(ABSENT_PLACEHOLDER);
  });
});

describe('formatFlag', () => {
  it('三态: null 是「没跑到」, 与 false 不是一回事', () => {
    expect(formatFlag(true)).toBe('是');
    expect(formatFlag(false)).toBe('否');
    expect(formatFlag(null)).toBe(ABSENT_PLACEHOLDER);
    expect(formatFlag(undefined)).toBe(ABSENT_PLACEHOLDER);
  });
});

describe('formatJsonText', () => {
  it('合法 JSON 缩进显示, 读不动就原样返回', () => {
    expect(formatJsonText('{"period":5}')).toBe('{\n  "period": 5\n}');
    expect(formatJsonText('不是 JSON')).toBe('不是 JSON');
    expect(formatJsonText('')).toBe(ABSENT_PLACEHOLDER);
    expect(formatJsonText(null)).toBe(ABSENT_PLACEHOLDER);
  });
});
