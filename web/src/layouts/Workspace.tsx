import {
  Activity,
  Bot,
  Layers3,
  Menu,
  PanelLeftClose,
  UsersRound,
} from 'lucide-react';
import { createContext, useContext, useState } from 'react';
import { Link, NavLink, Outlet } from 'umi';
import { Dialog, DialogContent, DialogHeading } from '@/components/ui/dialog';
import { useResource } from '@/lib/useResource';
import { api } from '@/services/api';

type WorkspaceValue = ReturnType<typeof useResource<API.ConfigResponse>>;
const WorkspaceContext = createContext<WorkspaceValue | null>(null);

/** 全局客户与处理配置只读取一份，页面保留各自的查询与选择状态。 */
export function useWorkspace() {
  const context = useContext(WorkspaceContext);
  if (!context) throw new Error('工作台尚未加载。');
  return context;
}

/** 桌面固定侧栏，窄屏用可访问弹层保留同一导航。 */
export default function Workspace() {
  // 客户计数与事件列表保持相同刷新频率，让首次产出的事件及时进入全部客户视图。
  const resource = useResource('workspace-config', api.config, 5000);
  const [open, setOpen] = useState(false);
  const navigation = (
    <nav className="main-nav" aria-label="工作台导航">
      <NavLink to="/events" onClick={() => setOpen(false)}>
        <Layers3 size={17} />
        工作台
      </NavLink>
      <NavLink to="/customers" onClick={() => setOpen(false)}>
        <UsersRound size={17} />
        客户管理
      </NavLink>
      <NavLink to="/tasks" onClick={() => setOpen(false)}>
        <Activity size={17} />
        任务中心
      </NavLink>
      <NavLink to="/settings/bots" onClick={() => setOpen(false)}>
        <Bot size={17} />
        机器人筛选
      </NavLink>
    </nav>
  );
  const brand = (
    <Link to="/events" className="brand" onClick={() => setOpen(false)}>
      <span className="brand-symbol">
        <Layers3 size={20} strokeWidth={1.8} />
      </span>
      <span>大客售后</span>
    </Link>
  );
  return (
    <WorkspaceContext.Provider value={resource}>
      <div className="workspace">
        <aside className="sidebar">
          {brand}
          {navigation}
        </aside>
        <div className="workspace-main">
          <header className="mobile-header">
            <button
              type="button"
              className="icon-button"
              onClick={() => setOpen(true)}
              aria-label="打开导航"
            >
              <Menu size={20} />
            </button>
            {brand}
          </header>
          <Outlet />
        </div>
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogContent className="navigation-dialog">
            <DialogHeading title="工作台导航" description="大客售后" />
            {navigation}
            <button
              type="button"
              className="button button-ghost"
              onClick={() => setOpen(false)}
            >
              <PanelLeftClose size={16} />
              收起导航
            </button>
          </DialogContent>
        </Dialog>
      </div>
    </WorkspaceContext.Provider>
  );
}
