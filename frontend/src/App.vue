<script setup lang="ts">
/**
 * 根组件: 外壳 + 路由出口.
 *
 * 路由出口放在外壳的插槽里, 于是顶栏在**页面切换之间不重建** —— 每次导航都重建一次导航条会
 * 让它的高亮与滚动位置闪一下.
 */

import { RouterView } from 'vue-router';

import AppLayout from './components/AppLayout.vue';
</script>

<template>
  <AppLayout>
    <RouterView />
  </AppLayout>
</template>
