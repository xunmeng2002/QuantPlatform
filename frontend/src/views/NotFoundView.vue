<script setup lang="ts">
/** 404. 是公开页: 一个已登录用户手抖敲错地址时, 不该被弹回登录页再折腾一次. */
import { ElButton, ElResult } from 'element-plus';
import { RouterLink } from 'vue-router';
</script>

<template>
  <div class="py-20">
    <!-- el-result 的 title 渲染在 <p> 里 (不是标题标签), 而这一页除了 "404" 之外没有别的 h1 ——
         只给一个视觉上的大字, 读屏用户就不知道"这一页是什么". 故补一个对读屏可见、屏幕上藏起来的
         h1; 两者措辞一致. -->
    <h1 class="sr-only">
      404, 这个地址没有对应的页面
    </h1>

    <ElResult
      icon="warning"
      title="404"
      sub-title="这个地址没有对应的页面"
    >
      <!-- tag="a" + href 而不是 @click="router.push": 与 /runs 的「新建回测」同一写法, 中键 /
           右键「在新标签页打开」是真实用法, 丢掉真锚点就没了. -->
      <template #extra>
        <RouterLink
          v-slot="{ navigate, href }"
          custom
          :to="{ name: 'runs' }"
        >
          <ElButton
            tag="a"
            type="primary"
            :href="href"
            @click="navigate"
          >
            回到回测运行
          </ElButton>
        </RouterLink>
      </template>
    </ElResult>
  </div>
</template>
