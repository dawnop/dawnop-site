<script setup>
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { authApi } from '../../api'
import { auth } from '../../store/auth'

const route = useRoute()
const router = useRouter()

const formRef = ref(null)
const form = ref({ username: '', password: '' })
const loading = ref(false)

const rules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

async function submit() {
  if (!formRef.value) return
  try {
    await formRef.value.validate()
  } catch (e) {
    return // 校验未通过
  }
  loading.value = true
  try {
    const { data } = await authApi.login(form.value.username, form.value.password)
    auth.setToken(data.access_token)
    ElMessage.success('登录成功')
    const redirect = route.query.redirect || '/admin'
    router.push(redirect)
  } catch (e) {
    ElMessage.error(e.response?.status === 401 ? '用户名或密码错误' : '登录失败，请稍后重试')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <el-card class="login-card" shadow="never">
      <img src="/logo.svg" alt="dawnop" class="login-logo" />
      <h1>dawnop 后台</h1>
      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-position="top"
        @submit.prevent="submit"
      >
        <el-form-item label="用户名" prop="username">
          <el-input v-model="form.username" autocomplete="username" placeholder="请输入用户名" />
        </el-form-item>
        <el-form-item label="密码" prop="password">
          <el-input
            v-model="form.password"
            type="password"
            show-password
            autocomplete="current-password"
            placeholder="请输入密码"
            @keyup.enter="submit"
          />
        </el-form-item>
        <el-button type="primary" class="submit" :loading="loading" @click="submit">
          登录
        </el-button>
      </el-form>
    </el-card>
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
  width: 360px;
  border-radius: 12px;
}
.login-logo {
  display: block;
  width: 56px;
  height: 56px;
  margin: 4px auto 12px;
}
.login-card h1 {
  margin: 0 0 18px;
  font-size: 1.3rem;
  text-align: center;
}
.submit {
  width: 100%;
  margin-top: 4px;
}
</style>
