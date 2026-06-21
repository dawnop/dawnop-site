<script setup>
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { authApi } from '../../api'
import { auth } from '../../store/auth'

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
  <div class="login-page">
    <div class="login-card">
      <h1>dawnop 后台</h1>
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
  </div>
</template>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f1f5f9;
}
.login-card {
  width: 340px;
  background: #fff;
  padding: 32px 28px;
  border-radius: 12px;
  box-shadow: 0 8px 30px rgba(0, 0, 0, 0.08);
}
.login-card h1 {
  margin: 0 0 18px;
  font-size: 1.3rem;
  text-align: center;
}
.submit {
  margin-top: 22px;
  width: 100%;
}
.error {
  color: #b91c1c;
  font-size: 0.9rem;
  margin: 12px 0 0;
}
</style>
