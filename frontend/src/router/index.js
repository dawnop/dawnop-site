import { createRouter, createWebHashHistory } from 'vue-router'
import { auth } from '../store/auth'
import PublicLayout from '../components/PublicLayout.vue'
import AdminLayout from '../components/AdminLayout.vue'

// 用 hash 路由：部署时无需为 SPA 配置 nginx try_files 回退。
const routes = [
  {
    path: '/',
    component: PublicLayout,
    children: [
      { path: '', name: 'home', component: () => import('../views/HomeView.vue') },
      {
        path: 'article/:slug',
        name: 'article',
        component: () => import('../views/ArticleView.vue'),
      },
      {
        path: 'p/:slug',
        name: 'page',
        component: () => import('../views/PageView.vue'),
      },
    ],
  },

  // 后台登录：独立，无侧边栏布局
  {
    path: '/admin/login',
    name: 'admin-login',
    component: () => import('../views/admin/LoginView.vue'),
  },

  // 后台：独立侧边栏布局，需登录
  {
    path: '/admin',
    component: AdminLayout,
    meta: { requiresAuth: true },
    children: [
      { path: '', redirect: '/admin/articles' },
      {
        path: 'articles',
        name: 'admin-articles',
        component: () => import('../views/admin/ArticlesAdminView.vue'),
      },
      {
        path: 'articles/new',
        name: 'admin-article-new',
        component: () => import('../views/admin/ArticleEditView.vue'),
      },
      {
        path: 'articles/:id/edit',
        name: 'admin-article-edit',
        component: () => import('../views/admin/ArticleEditView.vue'),
      },
      {
        path: 'pages',
        name: 'admin-pages',
        component: () => import('../views/admin/PagesAdminView.vue'),
      },
      {
        path: 'files',
        name: 'admin-files',
        component: () => import('../views/admin/FilesAdminView.vue'),
      },
    ],
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
  if (to.matched.some((r) => r.meta.requiresAuth) && !auth.isAuthenticated) {
    return { name: 'admin-login', query: { redirect: to.fullPath } }
  }
})

export default router
