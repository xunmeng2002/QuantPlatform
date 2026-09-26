import tailwindcss from '@tailwindcss/vite'
import vue from '@vitejs/plugin-vue'
// defineConfig 取自 vitest/config 而不是 vite: 它带回 `test` 键的类型, 于是构建与单测共用
// 这一份配置. 另建 vitest.config.ts 也行, 但那样配置就有两份, 改一处漏一处.
import { defineConfig } from 'vitest/config'

// 后端地址取自 backend/app/config.py 的 DEFAULT_HTTP_HOST / DEFAULT_HTTP_PORT.
// 写 127.0.0.1 而不是 localhost: Windows 上 localhost 先解析到 ::1, 而 uvicorn 只监听
// IPv4 的 127.0.0.1, 结果是代理层拿到 ECONNREFUSED —— 现象是"前端 502, 后端日志什么都没有".
const BACKEND_ORIGIN = 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [vue(), tailwindcss()],

  server: {
    port: 5173,
    proxy: {
      // 后端**没有**任何 CORS 中间件 (全仓 add_middleware 只出现在请求体大小闸上), 故开发期
      // 必须走同源代理. 不写 rewrite: 后端路由前缀本身就是 /api, 转发时路径须逐字保留——
      // 加一条 rewrite 就会把前缀削掉, 全站 404.
      '/api': {
        target: BACKEND_ORIGIN,
        changeOrigin: true,
      },
    },
  },

  test: {
    // 默认仍是 node 环境 (绝大多数用例测的是纯逻辑). 组件测试在**单个文件顶部**写
    // `// @vitest-environment jsdom`, 而不是把全局默认换掉 —— 逐文件声明时 Vitest 会把该文件的
    // SFC 按浏览器模式编译 (带 render 而不只是 ssrRender), 组件测试因此不必再自己搭 SSR 上下文.
    environment: 'node',
    // 必须用 POSIX 分隔符: Windows 上写成 'src\\**\\*.spec.ts' 会静默匹配不到任何文件, 且不报错.
    include: ['src/**/*.spec.ts'],
    // `vi.stubGlobal` 默认不会自动撤销. 不打开的话, 一个文件里桩掉的 fetch 会渗到同 worker 的
    // 其他用例乃至其他文件, 表现为随机失败.
    unstubGlobals: true,
    restoreMocks: true,
  },
})
