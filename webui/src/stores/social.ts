/**
 * 社交域缓存（W5 §17-§25、§114）。
 *
 * 只缓存当前页面需要的读取模型；人物/承诺等写入一律先落服务端再失效重取。
 */

import { ref } from 'vue'
import { defineStore } from 'pinia'

import { errorMessage } from '@/api/client'
import { socialApi } from '@/api/social'
import type {
  CommitmentDetail,
  CommitmentRow,
  GroupRow,
  RelationshipRow,
  SocialSessionRow,
  SocialSpaceRow,
  SocialUser,
  SocialUserDetail,
} from '@/types/social'

export const useSocialStore = defineStore('social', () => {
  const users = ref<SocialUser[]>([])
  const userTotal = ref<number | null>(null)
  const groups = ref<GroupRow[]>([])
  const relationships = ref<RelationshipRow[]>([])
  const commitments = ref<CommitmentRow[]>([])
  const sessions = ref<SocialSessionRow[]>([])
  const sessionActive = ref(false)
  const spaces = ref<SocialSpaceRow[]>([])
  const spaceMap = ref<Record<string, string>>({})
  const detail = ref<SocialUserDetail | null>(null)
  const commitmentDetail = ref<CommitmentDetail | null>(null)

  const loading = ref(false)
  const error = ref('')

  function fail(caught: unknown): void {
    error.value = errorMessage(caught)
  }

  async function loadUsers(options: { q?: string; limit?: number; offset?: number } = {}): Promise<void> {
    loading.value = true
    error.value = ''
    try {
      const page = await socialApi.users(options)
      users.value = page.items
      userTotal.value = page.total ?? page.items.length
    } catch (caught) {
      fail(caught)
    } finally {
      loading.value = false
    }
  }

  async function loadUser(personId: string): Promise<void> {
    loading.value = true
    error.value = ''
    try {
      detail.value = await socialApi.user(personId)
    } catch (caught) {
      detail.value = null
      fail(caught)
    } finally {
      loading.value = false
    }
  }

  async function loadGroups(): Promise<void> {
    try {
      const page = await socialApi.groups()
      groups.value = page.items
    } catch (caught) {
      fail(caught)
    }
  }

  async function loadRelationships(limit = 50): Promise<void> {
    try {
      const page = await socialApi.relationships(limit)
      relationships.value = page.items
    } catch (caught) {
      fail(caught)
    }
  }

  async function loadCommitments(options: { status?: string; person?: string } = {}): Promise<void> {
    try {
      const page = await socialApi.commitments(options)
      commitments.value = page.items
    } catch (caught) {
      fail(caught)
    }
  }

  async function loadCommitment(commitmentId: string): Promise<void> {
    try {
      commitmentDetail.value = await socialApi.commitment(commitmentId)
    } catch (caught) {
      commitmentDetail.value = null
      fail(caught)
    }
  }

  async function loadSessions(): Promise<void> {
    try {
      const data = await socialApi.sessions()
      sessions.value = data.items
      sessionActive.value = data.active
    } catch (caught) {
      fail(caught)
    }
  }

  async function loadSpaces(): Promise<void> {
    try {
      const data = await socialApi.spaces()
      spaces.value = data.items
      spaceMap.value = data.map
    } catch (caught) {
      fail(caught)
    }
  }

  async function saveUser(
    personId: string,
    payload: { nickname_override?: string; notes?: string; tags?: string[]; initiative_enabled?: boolean },
  ): Promise<boolean> {
    error.value = ''
    try {
      await socialApi.saveUser(personId, payload)
    } catch (caught) {
      fail(caught)
      return false
    }
    await Promise.all([loadUsers(), personId === detail.value?.person.person_id ? loadUser(personId) : Promise.resolve()])
    return true
  }

  async function setGroupParticipation(groupId: string, enabled: boolean): Promise<boolean> {
    error.value = ''
    try {
      await socialApi.setGroupParticipation(groupId, enabled)
    } catch (caught) {
      fail(caught)
      return false
    }
    await loadGroups()
    return true
  }

  function clear(): void {
    users.value = []
    groups.value = []
    relationships.value = []
    commitments.value = []
    sessions.value = []
    spaces.value = []
    detail.value = null
    commitmentDetail.value = null
    error.value = ''
  }

  return {
    users,
    userTotal,
    groups,
    relationships,
    commitments,
    sessions,
    sessionActive,
    spaces,
    spaceMap,
    detail,
    commitmentDetail,
    loading,
    error,
    loadUsers,
    loadUser,
    loadGroups,
    loadRelationships,
    loadCommitments,
    loadCommitment,
    loadSessions,
    loadSpaces,
    saveUser,
    setGroupParticipation,
    clear,
  }
})
