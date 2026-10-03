/** `src/pages/system` 测试的共享工具：信封 mock + 内存路由。 */

import { createMemoryHistory, createRouter, type RouteRecordRaw, type Router } from 'vue-router'

export {
  fail,
  flushAll,
  installFetch,
  makeField,
  ok,
  useFreshPinia,
  wait,
} from '@/components/config/__tests__/helpers'
export type { MockReply, MockRequest } from '@/components/config/__tests__/helpers'

export async function makeRouter(initial: string, routes: RouteRecordRaw[]): Promise<Router> {
  const router = createRouter({ history: createMemoryHistory(), routes })
  await router.push(initial)
  return router
}
