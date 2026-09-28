<script setup lang="ts">
/**
 * 主页 (公开落地页).
 *
 * 全站唯一"不需要登录也能看"的内容页: 讲清这是什么, 能做什么, 一轮回测怎么走, 引擎与运行环境
 * 是什么, 以及**当前边界在哪**. 内容全是静态文案, 因此这一页没有请求, 没有加载态, 也不碰任何反馈通道.
 *
 * 主视觉自绘而不是套 `PageHeader`: 那个组件是"工作页页头"原语 (布局是"标题 + 右侧动作"的
 * `justify-between`, 字号是工作页那一档), 而落地页的主视觉要更大的排版重心, 且这一页有五个同级
 * 分节 —— 套五个 `PageHeader` 会得到五处空位与五个"页头"语义. 全页仍只有一个 `h1`.
 *
 * 三处措辞上的自我约束, 改文案时别越过去:
 *   - 不写"云上": 上云是部署节奏 (本机先跑通, 再迁 Windows 云主机), 不是今天的能力;
 *   - 只写"尚未提供", 不写路线图式的"即将支持" —— 页面一落地, 边界卡里那一句就要跟着撤掉
 *     (多轮对比原在这句里, 2026-09-28 随 P6 一并删除);
 *   - 不介绍站点自己的实现 (前端框架, 后端框架, 进程与调度怎么接到一起): 读这一页的人要判断的是
 *     策略跑不跑得动, 多快, 产物是什么. 因此这一节只讲引擎侧.
 */

import { ElAlert, ElButton } from 'element-plus';
import { RouterLink } from 'vue-router';

import SurfaceCard from '../components/SurfaceCard.vue';
import { useSessionStore } from '../stores/session';

interface CapabilityCard {
  title: string;
  description: string;
  detail: string;
}

interface WorkflowStep {
  title: string;
  detail: string;
}

interface EngineEnvironmentRow {
  term: string;
  detail: string;
}

interface MeasuredStat {
  value: string;
  caption: string;
}

/**
 * 主视觉下方那三个数.
 *
 * 全是**实测值**, 且与「引擎与运行环境」一节里的两行同源 (改一处要两处一起改, 不许在这里"美化").
 * 落地页第一屏原本只有三行字, 没有任何可看的东西; 数字带是给它一个视觉落点, 顺带把"多快"这件事
 * 从一段文字变成一眼能扫到的东西.
 */
const measuredStats: readonly MeasuredStat[] = [
  { value: '3.8 秒', caption: '2010–2024 全段回测 (58176 根 bar, 678 笔成交)' },
  { value: '17 张', caption: '每轮落下的结果表, 逐笔明细可单独下载' },
  { value: '3.0 MB', caption: '单轮产物: 结果库, CSV 与日志' },
];

const session = useSessionStore();

const capabilityCards: readonly CapabilityCard[] = [
  {
    title: '上传策略, 按版本留档',
    description: '只收 .py 源码与一份 manifest.',
    detail:
      'manifest 声明入口文件, 配置文件与支持的行情模式, 平台据此校验. 内容没变就不产生新版本号: ' +
      '同一份代码传两次, 仍是同一个版本.',
  },
  {
    title: '参数表单由 manifest 生成',
    description: '策略作者改 manifest, 平台不用改一行代码.',
    detail:
      '参数的键, 标签, 类型, 默认值, 范围与枚举都写在 manifest 里, 提交页据此渲染表单; ' +
      '没有默认值的参数必须填, 越界与不在枚举里的取值当场被拒.',
  },
  {
    title: '排队运行, 随时可取消',
    description: '每轮一个独立工作目录, 队列按先来后到.',
    detail:
      '状态是排队中 / 运行中 / 已成功 / 已失败 / 已中断 / 已超时; 运行中的列表 2 秒刷新一次, ' +
      '不想跑了可以取消.',
  },
  {
    title: '结果可读, 产物可下',
    description: '绩效指标, 权益曲线与逐笔明细.',
    detail:
      '余额与可用资金, 交易与订单笔数, 三项费用; 逐日结算权益曲线与逐日回撤; ' +
      '资金 / 成交 / 委托 / 持仓 / 持仓明细 5 张明细分页表; 引擎的 stdout 与 stderr 尾部; ' +
      '每份产物单独下载.',
  },
];

const workflowSteps: readonly WorkflowStep[] = [
  {
    title: '上传策略',
    detail: '一份 .py 源码加一份 manifest. 平台校验 manifest 与源码, 通过后成为该策略的一个版本.',
  },
  {
    title: '选策略与版本',
    detail: '缺省选最新版本. 选中后参数表单按该版本的 manifest 生成, 并按你上次提交的那一份预填.',
  },
  {
    title: '填运行级字段',
    detail: '开始与结束交易日, 初始资金, K 线周期; 行情模式当前固定 Bar.',
  },
  {
    title: '排队执行',
    detail: '队列先来后到, 一轮占一个独立工作目录; 运行中可以取消, 也可以关掉页面去做别的.',
  },
  {
    title: '看结果与下载产物',
    detail: '绩效指标, 权益曲线与回撤, 5 张明细表, 引擎输出, 以及逐个下载的产物文件.',
  },
];

const engineEnvironmentRows: readonly EngineEnvironmentRow[] = [
  {
    term: '回测引擎',
    detail: '包裹 ../QuantTrading 的 C++ 引擎 (BackTest.dll); 平台拉起的是你上传的 Python 策略程序.',
  },
  {
    term: '运行环境',
    detail: 'Windows + Python 3.14: 引擎的 .pyd 是 cp314-win_amd64, Linux 上无解, 故后端必须与引擎同机.',
  },
  {
    term: '行情模式',
    detail: '当前只支持 Bar; manifest 可以声明 Tick, 但提交侧一律拒绝.',
  },
  {
    term: '一轮的产物',
    detail: 'result.json, 17 张表的结果库, 17 个 CSV 与日志, 单轮约 3.0 MB, 落在本机磁盘与 SQLite 上.',
  },
  {
    term: '实测速度',
    detail: '三个月的回测约 1.7 秒; 2010–2024 (58176 根 bar, 678 笔成交) 约 3.8 秒.',
  },
];

const boundaryNotes: readonly string[] = [
  '目录级隔离不是沙箱: 策略是任意 Python 程序, 平台挡得住界面越权与误访问, 挡不住恶意读盘. ' +
    '因此本期只对可信用户开放.',
  '单实例调度: 后端必须与引擎同机, 引擎的 .pyd 是 Windows 版, 横向扩展没有余地.',
  '队列是先来后到的纯 FIFO, 不设配额.',
  '重启后不做续跑: 重启时正在跑的轮会被标记为已中断.',
  '设置页尚未提供.',
];
</script>

<template>
  <div class="mx-auto max-w-5xl space-y-10">
    <section>
      <p class="text-sm font-medium text-brand">
        多用户的量化回测平台
      </p>
      <h1 class="mt-2 text-3xl font-semibold text-slate-900">
        薪火量化
      </h1>
      <p class="mt-3 max-w-2xl text-base leading-7 text-slate-600">
        把你写的 Python 策略传上来, 平台排队跑完回测引擎, 再把绩效, 权益曲线与逐笔明细摊开给你看.
      </p>

      <!-- 三个入口都用真锚点 (tag="a" + href): 与 /runs 的「新建回测」、404 的「回到回测运行」
           同一写法, 中键与右键「在新标签页打开」是真实用法. -->
      <div class="mt-6 flex flex-wrap items-center gap-3">
        <template v-if="session.hasCurrentUser">
          <RouterLink
            v-slot="{ navigate, href }"
            custom
            :to="{ name: 'runs' }"
          >
            <ElButton
              tag="a"
              type="primary"
              size="large"
              :href="href"
              @click="navigate"
            >
              进入回测运行
            </ElButton>
          </RouterLink>

          <RouterLink
            v-slot="{ navigate, href }"
            custom
            :to="{ name: 'run-submit' }"
          >
            <ElButton
              tag="a"
              size="large"
              :href="href"
              @click="navigate"
            >
              新建回测
            </ElButton>
          </RouterLink>
        </template>

        <template v-else>
          <RouterLink
            v-slot="{ navigate, href }"
            custom
            :to="{ name: 'login' }"
          >
            <ElButton
              tag="a"
              type="primary"
              size="large"
              :href="href"
              @click="navigate"
            >
              登录
            </ElButton>
          </RouterLink>

          <span class="text-sm text-slate-500">账号由管理员开通.</span>
        </template>
      </div>

      <!-- 每条 dt/dd 外各自套一层 div 是合法的: HTML5 允许 dl 用 div 分组. 分隔线画在这里而不是
           给每个数加边框 —— 三列在窄屏会折成一列, 逐列画线会折出一堆断头线. -->
      <div class="mt-10 border-t border-line pt-6">
        <p class="text-xs font-medium text-slate-500">
          本机实测
        </p>
        <dl class="mt-4 grid gap-x-8 gap-y-6 sm:grid-cols-3">
          <div
            v-for="stat in measuredStats"
            :key="stat.value"
            class="min-w-0"
          >
            <dt class="text-2xl font-semibold text-slate-900 tabular-nums">
              {{ stat.value }}
            </dt>
            <dd class="mt-1 text-xs leading-5 text-slate-500">
              {{ stat.caption }}
            </dd>
          </div>
        </dl>
      </div>
    </section>

    <section>
      <h2 class="text-xl font-semibold text-slate-900">
        平台能做什么
      </h2>
      <p class="mt-1 text-sm text-slate-500">
        管理员与普通用户各管各的: 策略可以选「仅自己 / 授权共享 / 全体可见」, 被授权的人可以直接提交运行,
        运行记录只有提交人本人能看到.
      </p>

      <div class="mt-4 grid gap-4 sm:grid-cols-2">
        <SurfaceCard
          v-for="card in capabilityCards"
          :key="card.title"
          :title="card.title"
          :description="card.description"
        >
          <p class="text-sm leading-6 text-slate-600">
            {{ card.detail }}
          </p>
        </SurfaceCard>
      </div>
    </section>

    <section>
      <h2 class="text-xl font-semibold text-slate-900">
        一次回测的完整流程
      </h2>
      <p class="mt-1 text-sm text-slate-500">
        从上传到看到结果, 中间不需要你守着.
      </p>

      <!-- 无标题形态: 卡上的 `title` 会带来一层正文包裹 `div`, 而 `<ol>` 的子元素只能是 `<li>`,
           套进去就是无效 HTML. 这一节的语境已经由上面的 `h2` 给足, 卡自身不需要再挂一个标题. -->
      <SurfaceCard class="mt-4">
        <ol class="space-y-4">
          <li
            v-for="(step, index) in workflowSteps"
            :key="step.title"
            class="flex gap-3"
          >
            <span
              class="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-brand/10 text-xs font-semibold text-brand"
              aria-hidden="true"
            >
              {{ index + 1 }}
            </span>
            <span class="min-w-0">
              <span class="block text-sm font-medium text-slate-800">{{ step.title }}</span>
              <span class="mt-0.5 block text-sm leading-6 text-slate-600">{{ step.detail }}</span>
            </span>
          </li>
        </ol>
      </SurfaceCard>
    </section>

    <section>
      <SurfaceCard title="引擎与运行环境">
        <dl class="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-2">
          <template
            v-for="row in engineEnvironmentRows"
            :key="row.term"
          >
            <dt class="font-medium text-slate-700">
              {{ row.term }}
            </dt>
            <dd class="text-slate-600">
              {{ row.detail }}
            </dd>
          </template>
        </dl>
      </SurfaceCard>
    </section>

    <ElAlert
      type="warning"
      :closable="false"
      title="当前边界"
    >
      <ul class="space-y-1 leading-6">
        <li
          v-for="note in boundaryNotes"
          :key="note"
        >
          {{ note }}
        </li>
      </ul>
    </ElAlert>
  </div>
</template>
