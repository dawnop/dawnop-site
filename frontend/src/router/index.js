import { createRouter, createWebHistory } from 'vue-router'
import { auth } from '../store/auth'
import { progressStart, progressDone } from '../progress'
import PublicLayout from '../components/PublicLayout.vue'
import AdminLayout from '../components/AdminLayout.vue'

// history 路由：URL 干净（/article/x，利于 SEO 与分享）。
// 代价：刷新/直达子路径需 nginx 把任意路径回退到 index.html（见 deploy/nginx.conf 的 try_files）。
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
      {
        path: '',
        name: 'admin-home',
        component: () => import('../views/admin/DashboardView.vue'),
      },
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
        path: 'pages/new',
        name: 'admin-page-new',
        component: () => import('../views/admin/PageEditView.vue'),
      },
      {
        path: 'pages/:id/edit',
        name: 'admin-page-edit',
        component: () => import('../views/admin/PageEditView.vue'),
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
  history: createWebHistory(),
  routes,
  scrollBehavior() {
    return { top: 0 }
  },
})

router.beforeEach((to) => {
  progressStart()
  if (to.matched.some((r) => r.meta.requiresAuth) && !auth.isAuthenticated) {
    // 本次导航将被重定向中止（不会触发 afterEach），先平衡掉计数；
    // 重定向到登录页会再次触发 beforeEach 重新计数。
    progressDone()
    return { name: 'admin-login', query: { redirect: to.fullPath } }
  }
})

// afterEach 在导航完成（含被「未保存」守卫取消的失败导航）后都会触发，保证收尾
router.afterEach(() => progressDone())
router.onError(() => progressDone())

export default router
