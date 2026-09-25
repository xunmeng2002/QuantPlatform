<script setup lang="ts">
/** 404. 是公开页: 一个已登录用户手抖敲错地址时, 不该被弹回登录页再折腾一次. */
import { RouterLink } from 'vue-router';
</script>

<template>
  <div class="py-20 text-center">
    <p class="text-3xl font-semibold text-slate-400">404</p>
    <p class="mt-2 text-sm text-slate-600">这个地址没有对应的页面</p>
    <RouterLink
      class="mt-4 inline-block rounded bg-brand px-4 py-2 text-sm font-medium text-white hover:bg-brand-strong"
      :to="{ name: 'runs' }"
    >
      回到回测运行
    </RouterLink>
  </div>
</template>
