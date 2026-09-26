<script setup lang="ts">
/**
 * 确认弹窗.
 *
 * 三处破坏性操作共用它: 删除策略、取消运行、停用账号. 自带「点遮罩即取消」, 且确认按钮默认不是
 * 危险色 —— 危险动作要由调用方显式声明, 免得随手复用时把「确定」染成红的.
 *
 * 自绘而不是用 el-message-box (2026-09-26 引入 Element Plus 后核对过, 结论仍是不换):
 * 形状不匹配 —— 本组件是**声明式** (isOpen prop + confirm/cancel emit + isBusy), MessageBox
 * 是**命令式 + Promise**, 换过去要把每个调用点「用 v-if 控制显隐」反转成 await 流程, 那是另一种
 * 迁移而不是机械替换; 而且真换过去会**顺带改掉行为** —— MessageBox 自带 ESC 关闭与焦点处理,
 * 本组件两样都没有 (只有「点遮罩即取消」与 role="dialog"), isBusy 也没有对应物. 这些差异要单独
 * 评估与验收, 故留后 (见 PROGRESS.md 的 D.11「不做 / 留后」).
 */

withDefaults(
  defineProps<{
    isOpen: boolean;
    title: string;
    message: string;
    confirmLabel?: string;
    isDangerous?: boolean;
    isBusy?: boolean;
  }>(),
  {
    confirmLabel: '确定',
    isDangerous: false,
    isBusy: false,
  },
);

const emit = defineEmits<{
  confirm: [];
  cancel: [];
}>();
</script>

<template>
  <div
    v-if="isOpen"
    class="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4"
    role="dialog"
    aria-modal="true"
    @click.self="emit('cancel')"
  >
    <div class="w-full max-w-md rounded-lg bg-surface p-5 shadow-xl">
      <h2 class="text-base font-semibold text-slate-900">{{ title }}</h2>
      <p class="mt-2 text-sm text-slate-600">{{ message }}</p>
      <div class="mt-5 flex justify-end gap-2">
        <button
          type="button"
          class="rounded border border-line px-3 py-1.5 text-sm hover:bg-slate-50"
          :disabled="isBusy"
          @click="emit('cancel')"
        >
          取消
        </button>
        <button
          type="button"
          class="rounded px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
          :class="isDangerous ? 'bg-rose-600 hover:bg-rose-700' : 'bg-brand hover:bg-brand-strong'"
          :disabled="isBusy"
          @click="emit('confirm')"
        >
          {{ isBusy ? '处理中…' : confirmLabel }}
        </button>
      </div>
    </div>
  </div>
</template>
