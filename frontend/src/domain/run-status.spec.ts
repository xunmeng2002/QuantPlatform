/**
 * 运行状态与引擎判定的显示.
 *
 * 「是否终态」决定列表页要不要继续轮询, 故它必须与后端 `catalog/enums.TERMINAL_RUN_STATUSES`
 * 逐项一致: 漏一个的后果是已经结束的轮被无限轮询, 多一个的后果是跑完的轮永远停在"运行中".
 * 下面两个用例都对 `RUN_STATUSES` 全量走一遍, 于是后端加了新状态而前端忘了跟, 这里就会转红.
 */

import { describe, expect, it } from 'vitest';

import { RUN_STATUSES } from '../api/types';
import type { RunStatus } from '../api/types';
import { describeEngineVerdict, describeRunStatus, isTerminalRunStatus } from './run-status';

const EXPECTED_LABELS: Record<RunStatus, string> = {
  queued: '排队中',
  running: '运行中',
  succeeded: '已成功',
  failed: '已失败',
  interrupted: '已中断',
  timeout: '已超时',
};

const EXPECTED_TERMINAL: Record<RunStatus, boolean> = {
  queued: false,
  running: false,
  succeeded: true,
  failed: true,
  interrupted: true,
  timeout: true,
};

describe('describeRunStatus', () => {
  it('每个状态都有自己的说法与色调', () => {
    for (const status of RUN_STATUSES) {
      const presentation = describeRunStatus(status);

      expect(presentation.label).toBe(EXPECTED_LABELS[status]);
      expect(presentation.tone).not.toBe('');
    }
  });

  it('终态用「已X」, 非终态用「X中」: 两者在界面上必须一眼分得出来', () => {
    expect(describeRunStatus('queued').label).toBe('排队中');
    expect(describeRunStatus('running').label).toBe('运行中');
    expect(describeRunStatus('succeeded').label).toBe('已成功');
  });
});

describe('isTerminalRunStatus', () => {
  it('逐个状态判定, 与后端的终态清单一致', () => {
    for (const status of RUN_STATUSES) {
      expect(isTerminalRunStatus(status)).toBe(EXPECTED_TERMINAL[status]);
    }
  });
});

describe('describeEngineVerdict', () => {
  it('成功 / 失败 / 没结论三种都分得开', () => {
    expect(describeEngineVerdict(true).label).toBe('引擎判定成功');
    expect(describeEngineVerdict(false).label).toBe('引擎判定失败');
    expect(describeEngineVerdict(null).label).toBe('引擎未给出结论');
    expect(describeEngineVerdict(undefined).label).toBe('引擎未给出结论');
  });

  it('「没结论」是中性的, 不是失败', () => {
    expect(describeEngineVerdict(null).tone).toBe('neutral');
  });
});
