/** 客户、事件和处理记录共用工作台入口，刷新详情时仍由同一页面恢复。 */
export default [
  {
    path: '/',
    component: '@/layouts/Workspace',
    routes: [
      { path: '/', redirect: '/events' },
      { path: '/events', component: './events' },
      { path: '/runs', redirect: '/tasks' },
      { path: '/tasks/run-detail', component: './tasks/run-detail' },
      { path: '/tasks', component: './tasks' },
      { path: '/customers', component: './customers' },
      { path: '/settings/bots', component: './settings/bots' },
      { path: '*', component: './not-found' },
    ],
  },
];
