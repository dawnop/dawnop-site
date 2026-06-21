import { createRouter, createWebHashHistory } from 'vue-router'
import { auth } from '../store/auth'

// 用 hash 路由：部署时无需为 SPA 配置 nginx try_files 回退。
const routes = [
  { path: '/', name: 'home', component: () => import('../views/HomeView.vue') },
  {
    path: '/article/:slug',
    name: 'article',
    component: () => import('../views/ArticleView.vue'),
  },
  { path: '/about', name: 'about', component: () => import('../views/AboutView.vue') },
  { path: '/login', name: 'login', component: () => import('../views/LoginView.vue') },

  {
    path: '/admin',
    redirect: '/admin/articles',
  },
  {
    path: '/admin/articles',
    name: 'admin-articles',
    component: () => import('../views/admin/ArticlesAdminView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/admin/articles/new',
    name: 'admin-article-new',
    component: () => import('../views/admin/ArticleEditView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/admin/articles/:id/edit',
    name: 'admin-article-edit',
    component: () => import('../views/admin/ArticleEditView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/admin/files',
    name: 'admin-files',
    component: () => import('../views/admin/FilesAdminView.vue'),
    meta: { requiresAuth: true },
  },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
  scrollBehavior() {
    return { top: 0 }
  },
})

router.beforeEach((to) => {
  if (to.meta.requiresAuth && !auth.isAuthenticated) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
})

export default router
