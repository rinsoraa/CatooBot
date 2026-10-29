/** 媒体页（W5 §39-§41）：贴纸过滤 / 删除确认 / 口癖操作 / 重新索引。 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'

import Media from '@/pages/abilities/Media.vue'
import { installFetch, ok, type MockReply, type MockRequest } from '@/components/config/__tests__/helpers'
import { useToast } from '@/composables/toast'
import type { ExpressionRow, StickerRow } from '@/types/domain'

const STICKERS: StickerRow[] = [
  {
    sticker_id: 's1',
    file: 'stickers/cat.png',
    file_name: 'cat.png',
    preview_url: null,
    emotion: 'happy',
    intent: 'greet',
    status: 'active',
    origin: 'collected',
    origin_user: '',
    usage_count: 3,
    last_used_at: null,
    created_at: null,
    safety_status: 'ok',
    valid: true,
  },
  {
    sticker_id: 's2',
    file: 'stickers/dog.png',
    file_name: 'dog.png',
    preview_url: null,
    emotion: 'sad',
    intent: '',
    status: 'disabled',
    origin: 'collected',
    origin_user: '',
    usage_count: 1,
    last_used_at: null,
    created_at: null,
    safety_status: 'ok',
    valid: false,
  },
]

const EXPRESSIONS: ExpressionRow[] = [
  {
    pattern_id: '1',
    pattern: '好耶',
    kind: 'catchphrase',
    status: 'active',
    group_id: 'g1',
    occurrences: 5,
    speakers: 2,
    first_seen: null,
    last_seen: null,
  },
]

let requests: MockRequest[] = []
let pinia: Pinia

function handler(request: MockRequest): MockReply {
  if (request.url.includes('/api/v1/stickers/reindex')) {
    return ok({ scanned: 12, added: null, updated: null, removed: 0, state: { done: 12 } })
  }
  if (request.url.includes('/api/v1/stickers/s1/delete')) {
    return ok({ sticker_id: 's1', action: 'delete', status: 'archived' })
  }
  if (request.url.includes('/api/v1/stickers')) {
    const filtered = request.url.includes('status=disabled') ? [STICKERS[1]] : STICKERS
    return ok({ items: filtered, stats: { total: filtered.length }, total: filtered.length })
  }
  if (request.url.includes('/api/v1/expressions/1/delete')) {
    return ok({ pattern_id: '1', action: 'delete', deleted: true })
  }
  if (request.url.includes('/api/v1/expressions/1/disable')) {
    return ok({ pattern_id: '1', action: 'disable', status: 'disabled' })
  }
  if (request.url.includes('/api/v1/expressions')) {
    return ok({ items: EXPRESSIONS, stats: { total: 1, active: 1, disabled: 0 } })
  }
  return { status: 404, payload: { ok: false, error: { code: 'x', message: `unmocked ${request.url}` } } }
}

async function settle(): Promise<void> {
  for (let index = 0; index < 8; index += 1) await flushPromises()
}

async function mountPage(): Promise<VueWrapper> {
  const wrapper = mount(Media, { global: { plugins: [pinia] } })
  await settle()
  return wrapper
}

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  useToast().clear()
  requests = installFetch(handler)
})

describe('Media 页', () => {
  it('renders the real sticker library with file-name placeholders and the honesty note', async () => {
    const wrapper = await mountPage()

    expect(wrapper.get('[data-test="media-stickers"]').text()).toContain(
      '只有真实贴纸库里的内容会出现在这里，普通图片不会被自动标记为贴纸',
    )
    const cards = wrapper.findAll('[data-test="sticker-card"]')
    expect(cards).toHaveLength(2)
    // preview_url 恒为 null → 用文件名占位
    expect(cards[0]?.text()).toContain('cat.png')
    expect(cards[0]?.text()).toContain('happy')
    expect(cards[1]?.get('[data-test="sticker-valid"]').text()).toContain('无法解析')

    const list = requests.find((request) => request.url.includes('/api/v1/stickers'))
    expect(list?.method).toBe('GET')
  })

  it('filters stickers through the server query', async () => {
    const wrapper = await mountPage()

    await wrapper.get('[data-test="sticker-status"]').setValue('disabled')
    await settle()

    const filtered = requests.filter((request) => request.url.includes('/api/v1/stickers'))
    expect(filtered.at(-1)?.url).toContain('status=disabled')
    expect(wrapper.findAll('[data-test="sticker-card"]')).toHaveLength(1)
    expect(wrapper.get('[data-test="sticker-card"]').text()).toContain('dog.png')
  })

  it('requires a confirm before deleting a sticker and sends confirm=delete', async () => {
    const wrapper = await mountPage()

    await wrapper.findAll('[data-test="sticker-delete"]')[0]?.trigger('click')
    expect(wrapper.find('[role="dialog"]').exists()).toBe(true)
    expect(requests.some((request) => request.method === 'POST')).toBe(false)

    await wrapper.get('[data-test="confirm"]').trigger('click')
    await settle()

    const remove = requests.find((request) => request.method === 'POST')
    expect(remove?.url).toContain('/api/v1/stickers/s1/delete')
    expect(remove?.body).toEqual({ confirm: 'delete' })
  })

  it('requires a confirm before reindexing and shows the scanned count', async () => {
    const wrapper = await mountPage()

    await wrapper.get('[data-test="sticker-reindex"]').trigger('click')
    await wrapper.get('[data-test="confirm"]').trigger('click')
    await settle()

    const reindex = requests.find((request) => request.url.includes('/stickers/reindex'))
    expect(reindex?.method).toBe('POST')
    expect(wrapper.get('[data-test="reindex-result"]').text()).toContain('扫描 12 条')
  })

  it('disables and deletes expressions with confirmations', async () => {
    const wrapper = await mountPage()

    expect(wrapper.get('[data-test="expressions-table"]').text()).toContain('好耶')
    await wrapper.get('[data-test="expression-disable"]').trigger('click')
    await settle()

    const disable = requests.find((request) => request.url.includes('/expressions/1/disable'))
    expect(disable?.method).toBe('POST')

    await wrapper.get('[data-test="expression-delete"]').trigger('click')
    expect(wrapper.find('[role="dialog"]').exists()).toBe(true)
    await wrapper.get('[data-test="confirm"]').trigger('click')
    await settle()

    const remove = requests.find((request) => request.url.includes('/expressions/1/delete'))
    expect(remove?.method).toBe('POST')
    expect(remove?.body).toEqual({ confirm: 'delete' })
  })
})
