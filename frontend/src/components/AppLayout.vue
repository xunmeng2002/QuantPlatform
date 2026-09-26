<script setup lang="ts">
/**
 * 页面外壳: 顶栏 + 内容区.
 *
 * 顶栏在 `meta.hidesHeader` 的页面 (登录页、404 页) 上整个不渲染: 那里既没有身份可显示, 也没有
 * 可以安全点过去的链接. 判据刻意**不是** `meta.isPublic` —— 主页是公开的但要看得到顶栏 (访客
 * 要从那里登录), 而"不需要登录"与"不挂外壳"本来就是两件事.
 *
 * 用户区按"身份已就绪"二选一: 拿到用户才渲染头像与显示名, 否则渲染「登录」. 只按令牌判断的话,
 * 公开页上的访客会看到一个空名字加一个 `?` 头像, 旁边还挂着一个点了只会跳登录页的「退出登录」.
 *
 * 内容宽度 `max-w-7xl` (1280px) 与内边距 `px-6` 是全站的统一口径: 表格此前挤在 1152px 里是
 * 观感显旧的主因之一.
 */

import { computed } from 'vue';
import { ElButton } from 'element-plus';
import { RouterLink, useRoute, useRouter } from 'vue-router';

import StatusBadge from './StatusBadge.vue';

import { describeUserType } from '../domain/labels';
import type { StatusPresentation } from '../domain/run-status';
import { useSessionStore } from '../stores/session';

const route = useRoute();
const router = useRouter();
const session = useSessionStore();

interface NavigationLink {
  routeName: string;
  label: string;
}

const isHeaderVisible = computed(() => route.meta.hidesHeader !== true);

/** 用户管理是管理员专属, 故链接也跟着身份走 —— 普通用户看到一个点了会被弹回来的入口没有意义. */
const navigationLinks = computed<NavigationLink[]>(() => {
  const links: NavigationLink[] = [
    { routeName: 'runs', label: '回测运行' },
    { routeName: 'strategies', label: '策略' },
  ];

  if (session.isAdmin) {
    links.push({ routeName: 'users', label: '用户管理' });
  }

  return links;
});

/**
 * 管理员在顶栏挂一个角色标签.
 *
 * 走 `StatusBadge` 而不是手搓 `ElTag`: 「色调 → 表现」的映射全仓只有它一处, 这里再写一遍就会
 * 出现第二份. 形状也照抄列表页那个 `v-bind="describeRunStatus(row.status)"`.
 */
const adminRoleBadge = computed<StatusPresentation | null>(() => {
  const currentUser = session.currentUser;

  if (currentUser === null || currentUser.user_type !== 'admin') {
    return null;
  }

  return { label: describeUserType(currentUser.user_type), tone: 'neutral' };
});

/**
 * 顶栏标记的地址.
 *
 * 走 `:src` 绑定而**不是** `src="/favicon.svg"` 字面量: 静态属性会被 `@vitejs/plugin-vue` 的
 * `transformAssetUrls` 改写成一句 `import '/favicon.svg'` (该插件在没有 dev server 时用
 * `includeAbsolute: true`), 而 `public/` 里的文件并不参与打包 —— 生产构建侥幸能过, 但 Vitest
 * 会把它解析成 `file:///favicon.svg` 并当场抛 `fileURLToPath` (Windows 下该形式非法), 外壳的
 * 两份 spec 连一条用例都跑不到. 指令绑定不在改写范围内, 公共目录的 URL 就该原样交给浏览器.
 */
const brandMarkUrl = '/favicon.svg';

/** 头像位只放首字. 用 `Array.from` 而不是 `[0]`: 显示名可能以表情等代理对字符开头. */
const avatarInitial = computed(() => Array.from(session.displayName.trim())[0] ?? '?');

async function signOut(): Promise<void> {
  session.clear();
  await router.push({ name: 'login' });
}
</script>

<template>
  <div class="min-h-screen bg-page text-slate-800">
    <!--
      粘性顶栏用**不透明**底色, 刻意不写 `backdrop-blur`.
      毛玻璃要 `bg-surface/90` + `backdrop-filter`, 后者会建合成层并在每个滚动帧重算模糊区; 而
      /runs 只要存在未完成的运行就每 2 秒整表重画一次, 两者叠起来在核显机器上是掉帧, 不只是观感
      问题. 收益 (白底页面上几乎看不见的一层通透) 远小于这个代价, 故取不透明 + 一条分隔线.
    -->
    <header
      v-if="isHeaderVisible"
      class="sticky top-0 z-40 border-b border-line bg-surface"
    >
      <div class="mx-auto flex max-w-7xl flex-wrap items-center gap-4 px-6 py-3">
        <!-- 品牌区指回主页: 它是"回首页"这个惯例语义, 而导航项表达的是"你在哪个工作面".
             两者在这份模板里本来就不重叠 —— 品牌区没有 `active-class`, <nav> 才有. -->
        <RouterLink
          :to="{ name: 'home' }"
          class="flex items-center gap-2"
        >
          <!-- 标记直接引标签页图标那一份文件 (public/favicon.svg), 不在这里复刻一份 SVG: 两处几何数据
               早晚漂移, 而"应用里的标"与"标签页上的标"本来就该是同一个. 换成几何标记是因为汉字
               「薪」有 17 画, 塞进 28px 方块里笔画会挤成一团 (D.13 就记过这条). 圆角在文件里已经
               画好了, 这里不再叠 `rounded-md`; `alt=""` 是因为它纯装饰 —— 紧挨着的「薪火量化」四个
               字才是这一项的可访问名. 地址为什么走变量见 `brandMarkUrl`. -->
          <img
            :src="brandMarkUrl"
            alt=""
            class="h-7 w-7"
          >
          <span class="text-sm font-semibold text-slate-900">薪火量化</span>
        </RouterLink>

        <nav class="flex flex-1 flex-wrap gap-1">
          <RouterLink
            v-for="link in navigationLinks"
            :key="link.routeName"
            :to="{ name: link.routeName }"
            class="rounded-md px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-100"
            active-class="bg-brand/10 font-semibold text-brand"
          >
            {{ link.label }}
          </RouterLink>
        </nav>

        <template v-if="session.hasCurrentUser">
          <StatusBadge
            v-if="adminRoleBadge"
            v-bind="adminRoleBadge"
          />

          <span class="flex items-center gap-2">
            <span
              class="flex h-7 w-7 items-center justify-center rounded-full bg-slate-200 text-xs font-semibold text-slate-700"
              aria-hidden="true"
            >
              {{ avatarInitial }}
            </span>
            <span class="text-sm text-slate-600">{{ session.displayName }}</span>
          </span>

          <ElButton
            size="small"
            @click="signOut"
          >
            退出登录
          </ElButton>
        </template>

        <!-- tag="a" + href 而不是 @click="router.push": 与 404 页「回到回测运行」同一写法, 中键 /
             右键「在新标签页打开」是真实用法, 丢掉真锚点就没了. -->
        <RouterLink
          v-else
          v-slot="{ navigate, href }"
          custom
          :to="{ name: 'login' }"
        >
          <ElButton
            tag="a"
            size="small"
            type="primary"
            :href="href"
            @click="navigate"
          >
            登录
          </ElButton>
        </RouterLink>
      </div>
    </header>

    <!--
      `min-h-[60vh]` 是页面过渡的必需品: `mode="out-in"` 在离场那 150ms 里只渲染一个注释占位,
      内容高度会塌到只剩顶栏, 浏览器顺手把滚动位置截到顶部, 表现就是"先跳一下再落回".
    -->
    <main class="mx-auto min-h-[60vh] max-w-7xl px-6 py-8">
      <slot />
    </main>
  </div>
</template>
