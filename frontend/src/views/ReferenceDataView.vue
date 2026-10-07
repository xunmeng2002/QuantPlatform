<script setup lang="ts">
/**
 * 基础数据: 引擎启动时要读的那三张表 (品种 / 手续费组 / 费率).
 *
 * 数据住在平台的库里, 而引擎每一轮读的是**该轮作业目录里**的一份小种子库 —— 平台在运行前按这
 * 三张表生成它 (费率的三级规则在那里被摊成具体合约的行), 故这一页只有 CRUD, 没有"同步"按钮, 也
 * 没有状态卡: 盘上没有一份需要人工维护的全局文件.
 *
 * 三个标签页用 `lazy`: 不加的话进页面就会同时发三个列表请求, 而用户此刻只看得到第一个 —— 那是
 * 两次白花的往返, 且它们的失败横幅会一起冒出来, 像是三处同时坏了.
 */

import { ElTabPane, ElTabs } from 'element-plus';

import PageHeader from '../components/PageHeader.vue';
import ReferenceBaseCommissionPanel from '../components/ReferenceBaseCommissionPanel.vue';
import ReferenceCommissionGroupPanel from '../components/ReferenceCommissionGroupPanel.vue';
import ReferenceProductPanel from '../components/ReferenceProductPanel.vue';
</script>

<template>
  <section>
    <PageHeader
      title="基础数据"
      description="引擎启动时读的那三张表; 费率可按合约 / 品种 / 交易所三级设置"
    />

    <!-- `default-value` 必须显式给: `ElTabs` 的默认激活项是**序号** `'0'`, 而这里的三个面板各有
         名字 (`products` / …) —— 不指一次, 就没有任何一个面板匹配得上, 于是三页全是空的, 而标签
         条本身照常渲染 (看上去像"面板没内容", 不像"选错了页"). -->
    <ElTabs default-value="products">
      <ElTabPane
        label="品种"
        name="products"
        lazy
      >
        <ReferenceProductPanel />
      </ElTabPane>

      <ElTabPane
        label="手续费组"
        name="commission-groups"
        lazy
      >
        <ReferenceCommissionGroupPanel />
      </ElTabPane>

      <ElTabPane
        label="费率明细"
        name="base-commissions"
        lazy
      >
        <ReferenceBaseCommissionPanel />
      </ElTabPane>
    </ElTabs>
  </section>
</template>
