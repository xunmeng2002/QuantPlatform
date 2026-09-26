<script setup lang="ts">
/**
 * 根组件: 语言配置 + 外壳 + 路由出口.
 *
 * 路由出口放在外壳的插槽里, 于是顶栏在**页面切换之间不重建** —— 每次导航都重建一次导航条会
 * 让它的高亮与滚动位置闪一下.
 *
 * `ElConfigProvider` 必须包在 `AppLayout` **外面**: 本项目不做 `app.use(ElementPlus)` 全量注册
 * (那会废掉摇树), locale 只能靠这一层注入, 而它只对**子树**生效 —— 放进去的话导航栏自己那一层
 * 就看不到.
 */

import { ElConfigProvider } from 'element-plus';
import zhCn from 'element-plus/es/locale/lang/zh-cn';

import { RouterView } from 'vue-router';

import AppLayout from './components/AppLayout.vue';
</script>

<template>
  <ElConfigProvider :locale="zhCn">
    <AppLayout>
      <RouterView />
    </AppLayout>
  </ElConfigProvider>
</template>
