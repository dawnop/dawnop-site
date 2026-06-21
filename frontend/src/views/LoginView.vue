<script setup>
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { authApi } from '../api'
import { auth } from '../store/auth'

const route = useRoute()
const router = useRouter()

const username = ref('')
const password = ref('')
const error = ref('')
const loading = ref(false)

async function submit() {
  error.value = ''
  loading.value = true
  try {
    const { data } = await authApi.login(username.value, password.value)
    auth.setToken(data.access_token)
    const redirect = route.query.redirect || '/admin/articles'
    router.push(redirect)
  } catch (e) {
    error.value = e.response?.status === 401 ? '用户名或密码错误' : '登录失败'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login">
    <h1>登录</h1>
    <form @submit.prevent="submit">
      <label for="u">用户名</label>
      <input id="u" v-model="username" autocomplete="username" />
      <label for="p">密码</label>
      <input id="p" v-model="password" type="password" autocomplete="current-password" />
      <p v-if="error" class="error">{{ error }}</p>
      <button class="primary submit" type="submit" :disabled="loading">
        {{ loading ? '登录中…' : '登录' }}
      </button>
    </form>
  </div>
</template>

<style scoped>
.login {
  max-width: 360px;
  margin: 40px auto;
}
.submit {
  margin-top: 20px;
  width: 100%;
}
.error {
  color: #b91c1c;
  font-size: 0.9rem;
  margin: 12px 0 0;
}
</style>
