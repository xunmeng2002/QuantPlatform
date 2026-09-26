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
 *
 * `:message="{ max: 3 }"` 是 toast 的并发上限. `max` **不是** `ElMessage` 的 per-call 选项
 * (2.14.6 的 `messageProps` 里没有这一项, 传了只会作为废属性漏进 DOM), 它只由 provider 写进
 * 模块级的 `messageConfig` 再被读到; 那条 watch 带 `immediate: true`, 故在根上给一次就够.
 *
 * 页面过渡放在 `RouterView` 的插槽里, 键取 `route.path` —— **不含 query**, 于是
 * `/runs?offset=20` 这类翻页不重挂载页面 (重挂载会清空列表并重新取数, 翻页要闪一下), 只有真正
 * 换页面才过渡. 过渡的类名在 `style.css` 的 `.page-*` 里.
 */

import { ElConfigProvider } from 'element-plus';
import zhCn from 'element-plus/es/locale/lang/zh-cn';

import { RouterView } from 'vue-router';

import AppLayout from './components/AppLayout.vue';
</script>

<template>
  <ElConfigProvider :locale="zhCn" :message="{ max: 3 }">
    <AppLayout>
      <RouterView v-slot="{ Component, route }">
        <Transition name="page" mode="out-in">
          <component :is="Component" :key="route.path" />
        </Transition>
      </RouterView>
    </AppLayout>
  </ElConfigProvider>
</template>
