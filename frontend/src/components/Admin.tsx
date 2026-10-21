import { t, useI18n, currentLocale } from '../i18n';
import { useState, useEffect } from "react"
import { api } from "../lib/api"
import { ArrowLeft } from "lucide-react"
import "../styles/panels.css"
import { Brand } from "./Brand"

interface Stats {
  users: { total: number; today: number; blocked: number; admins: number; moderators: number; max_admins: number }
  channels: { total: number }
  messages: { total: number; today: number; week: number }
  posts: { total: number }
  files: { total: number; storage_bytes: number }
}

interface User {
  id: string
  name: string
  email: string
  role: string
  is_blocked: boolean
  created_at: string
}

interface Channel {
  id: string
  name: string
  type: string
  created_at: string
}

interface MyRole {
  id: string
  name: string
  role: string
  is_staff: boolean
  is_admin: boolean
}

interface AdminProps {
  onBack: () => void
}

export function Admin({ onBack }: AdminProps) {
  useI18n();
  const [stats, setStats] = useState<Stats | null>(null)
  const [users, setUsers] = useState<User[]>([])
  const [channels, setChannels] = useState<Channel[]>([])
  const [myRole, setMyRole] = useState<MyRole | null>(null)
  const [tab, setTab] = useState<"stats" | "users" | "channels">("stats")
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  
  // Add user form
  const [showAddUser, setShowAddUser] = useState(false)
  const [newUserName, setNewUserName] = useState("")
  const [newUserEmail, setNewUserEmail] = useState("")
  const [newUserPassword, setNewUserPassword] = useState("")
  const [newUserRole, setNewUserRole] = useState("user")

  useEffect(() => {
    checkRole()
  }, [])

  const checkRole = async () => {
    try {
      const role = await api.request<MyRole>("/admin/me")
      setMyRole(role)
      if (role.is_staff) {
        loadData()
      } else {
        setLoading(false)
      }
    } catch {
      setLoading(false)
    }
  }

  const loadData = async () => {
    setLoading(true)
    setError(null)
    try {
      const [statsData, usersData, channelsData] = await Promise.all([
        api.request<Stats>("/admin/stats"),
        api.request<User[]>("/admin/users"),
        api.request<Channel[]>("/admin/channels")
      ])
      setStats(statsData)
      setUsers(usersData)
      setChannels(channelsData)
    } catch (e: any) {
      setError(e.message || t("Failed to load admin data"))
    }
    setLoading(false)
  }

  const createUser = async () => {
    if (!newUserName || !newUserEmail || !newUserPassword) {
      alert(t("Please fill all fields"))
      return
    }
    try {
      await api.request("/admin/users", {
        method: "POST",
        body: JSON.stringify({
          name: newUserName,
          email: newUserEmail,
          password: newUserPassword,
          role: newUserRole
        })
      })
      setShowAddUser(false)
      setNewUserName("")
      setNewUserEmail("")
      setNewUserPassword("")
      setNewUserRole("user")
      loadData()
    } catch (e: any) {
      alert(e.message)
    }
  }

  const toggleBlock = async (userId: string) => {
    try {
      await api.request("/admin/users/" + userId + "/block", { method: "PATCH" })
      loadData()
    } catch (e: any) {
      alert(e.message)
    }
  }

  const setRole = async (userId: string, role: string) => {
    try {
      await api.request("/admin/users/" + userId + "/role?role=" + role, { method: "PATCH" })
      loadData()
    } catch (e: any) {
      alert(e.message)
    }
  }

  const formatBytes = (bytes: number) => {
    if (bytes < 1024) return bytes + " B"
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB"
    return (bytes / (1024 * 1024)).toFixed(1) + " MB"
  }

  const getRoleBadge = (role: string) => {
    switch (role) {
      case "admin": return "fc-badge fc-badge-accent"
      case "moderator": return "fc-badge fc-badge-accent"
      default: return "fc-badge"
    }
  }

  if (loading) {
    return (
      <div className="fc-admin fc-admin-centered">
        <div className="fc-panel-spinner" />
      </div>
    )
  }

  if (!myRole?.is_staff) {
    return (
      <div className="fc-admin fc-admin-centered">
        <div className="fc-admin-access">
          <h2 className="text-xl font-semibold mb-4">{t("Admin Access")}</h2>
          {error ? (
            <div className="fc-panel-error mb-4">{error}</div>
          ) : (
            <p className="fc-muted mb-6">
              {t("You are logged in as ")}<span className="fc-text">{myRole?.name}</span>.<br/>
              {t("Ask your administrator for access.")}</p>
          )}
          <div className="flex gap-3 justify-center">
            <button onClick={onBack} className="fc-button">
              {t("Go Back")}</button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="fc-admin">
      <div className="fc-admin-header">
        <div className="flex flex-wrap items-center gap-4">
          <button onClick={onBack} className="fc-button"><ArrowLeft size={16} aria-hidden="true" />{t("Back")}</button>
          <h1 className="text-xl font-bold">{t("Admin Panel")}</h1>
          <span className={"px-2 py-1 rounded text-xs " + getRoleBadge(myRole.role)}>
            {myRole.role.toUpperCase()}
          </span>
        </div>
        <div className="fc-admin-brand"><Brand size="small" /><span>{t("Administration · v0.8.0")}</span></div>
      </div>

      <div className="fc-admin-tabs">
        {(["stats", "users", "channels"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={"fc-admin-tab " + (tab === t ? "is-active" : "")}
            aria-pressed={tab === t}
          >
            {t}
          </button>
        ))}
      </div>

      <div className="fc-admin-content">
        {tab === "stats" && stats && (
          <div className="space-y-6">
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
              <StatCard title={t("Users")} value={stats.users.total} sub={stats.users.today + " today"} />
              <StatCard title={t("Staff")} value={stats.users.admins + stats.users.moderators} sub={stats.users.admins + "/" + stats.users.max_admins + " admins, " + stats.users.moderators + " mods"} />
              <StatCard title={t("Blocked")} value={stats.users.blocked} />
              <StatCard title={t("Channels")} value={stats.channels.total} />
              <StatCard title={t("Messages")} value={stats.messages.total} sub={stats.messages.today + " today"} />
              <StatCard title={t("Posts")} value={stats.posts.total} />
              <StatCard title={t("Files")} value={stats.files.total} sub={formatBytes(stats.files.storage_bytes)} />
            </div>
            
            <div className="fc-admin-card">
              <h3 className="text-lg font-semibold mb-4">{t("Role Permissions")}</h3>
              <div className="grid md:grid-cols-3 gap-4 text-sm">
                <div className="fc-permission-card">
                  <div className="font-semibold fc-accent mb-2">{t("Admin (max 5)")}</div>
                  <ul className="fc-muted space-y-1">
                    <li>{t("Full system control")}</li>
                    <li>{t("Add new users")}</li>
                    <li>{t("Promote/demote roles")}</li>
                    <li>{t("Pause or unblock user access")}</li>
                    <li>{t("See everything")}</li>
                    <li>{t("All moderator powers")}</li>
                  </ul>
                </div>
                <div className="fc-permission-card">
                  <div className="font-semibold fc-accent mb-2">{t("Moderator (unlimited)")}</div>
                  <ul className="fc-muted space-y-1">
                    <li>{t("View stats and users")}</li>
                    <li>{t("Pause/block users")}</li>
                    <li>{t("Unblock users")}</li>
                    <li>{t("Delete messages")}</li>
                    <li>{t("Review channels")}</li>
                  </ul>
                </div>
                <div className="fc-permission-card">
                  <div className="font-semibold fc-muted mb-2">{t("User")}</div>
                  <ul className="fc-muted space-y-1">
                    <li>{t("Chat and messaging")}</li>
                    <li>{t("Create channels")}</li>
                    <li>{t("Upload files")}</li>
                    <li>{t("No admin access")}</li>
                  </ul>
                </div>
              </div>
            </div>
          </div>
        )}

        {tab === "users" && (
          <div className="space-y-4">
            {myRole.is_admin && (
              <div className="flex justify-end">
                <button 
                  onClick={() => setShowAddUser(true)} 
                  className="fc-button fc-button-primary"
                >
                  {t("+ Add User")}</button>
              </div>
            )}
            
            {showAddUser && (
              <div className="fc-admin-card">
                <h3 className="text-lg font-semibold mb-4">{t("Create New User")}</h3>
                <div className="grid md:grid-cols-2 gap-4">
                  <input
                    type="text"
                    aria-label={t("Name")}
                    placeholder={t("Name")}
                    value={newUserName}
                    onChange={(e) => setNewUserName(e.target.value)}
                    className="fc-input"
                  />
                  <input
                    type="email"
                    aria-label={t("Email")}
                    placeholder={t("Email")}
                    value={newUserEmail}
                    onChange={(e) => setNewUserEmail(e.target.value)}
                    className="fc-input"
                  />
                  <input
                    type="password"
                    aria-label={t("Password")}
                    placeholder={t("Password")}
                    value={newUserPassword}
                    onChange={(e) => setNewUserPassword(e.target.value)}
                    className="fc-input"
                  />
                  <select
                    aria-label={t("New user role")}
                    value={newUserRole}
                    onChange={(e) => setNewUserRole(e.target.value)}
                    className="fc-input"
                  >
                    <option value="user">{t("User")}</option>
                    <option value="moderator">{t("Moderator")}</option>
                    <option value="admin" disabled={!!(stats && stats.users.admins >= stats.users.max_admins)}>
                      {t("Admin ")}{stats && stats.users.admins >= stats.users.max_admins ? t("(max reached)") : ""}
                    </option>
                  </select>
                </div>
                <div className="flex gap-2 mt-4">
                  <button onClick={createUser} className="fc-button fc-button-primary">
                    {t("Create User")}</button>
                  <button onClick={() => setShowAddUser(false)} className="fc-button">
                    {t("Cancel")}</button>
                </div>
              </div>
            )}
            
            <div className="fc-admin-table-wrap">
              <table className="w-full">
                <thead className="fc-table-heading">
                  <tr>
                    <th className="px-4 py-3 text-left">{t("Name")}</th>
                    <th className="px-4 py-3 text-left">{t("Email")}</th>
                    <th className="px-4 py-3 text-left">{t("Role")}</th>
                    <th className="px-4 py-3 text-left">{t("Status")}</th>
                    <th className="px-4 py-3 text-left">{t("Actions")}</th>
                  </tr>
                </thead>
                <tbody>
                  {users.map((user) => (
                    <tr key={user.id} className="fc-table-row">
                      <td className="px-4 py-3">{user.name}</td>
                      <td className="px-4 py-3 fc-muted">{user.email}</td>
                      <td className="px-4 py-3">
                        {myRole.is_admin ? (
                          <select 
                            aria-label={t("Role for {value0}", { value0: String(user.name) })}
                            value={user.role} 
                            onChange={(e) => setRole(user.id, e.target.value)} 
                            className="fc-input"
                            disabled={user.id === myRole.id}
                          >
                            <option value="user">{t("User")}</option>
                            <option value="moderator">{t("Moderator")}</option>
                            <option value="admin" disabled={!!(stats && stats.users.admins >= stats.users.max_admins && user.role !== "admin")}>
                              {t("Admin ")}{stats && stats.users.admins >= stats.users.max_admins && user.role !== "admin" ? t("(max)") : ""}
                            </option>
                          </select>
                        ) : (
                          <span className={"px-2 py-1 rounded text-sm " + getRoleBadge(user.role)}>
                            {user.role}
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        <span className={"fc-badge " + (user.is_blocked ? "fc-badge-danger" : "fc-badge-accent")}>
                          {user.is_blocked ? t("Blocked") : t("Active")}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        {user.id !== myRole.id && (myRole.is_admin || user.role === "user") && (
                          <button 
                            onClick={() => toggleBlock(user.id)} 
                            className="fc-button"
                          >
                            {user.is_blocked ? t("Unblock") : t("Pause")}
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {tab === "channels" && (
          <div className="fc-admin-table-wrap">
            <table className="w-full">
              <thead className="fc-table-heading">
                <tr>
                  <th className="px-4 py-3 text-left">{t("Name")}</th>
                  <th className="px-4 py-3 text-left">{t("Type")}</th>
                  <th className="px-4 py-3 text-left">{t("Created")}</th>
                </tr>
              </thead>
              <tbody>
                {channels.map((channel) => (
                  <tr key={channel.id} className="fc-table-row">
                    <td className="px-4 py-3">{channel.name}</td>
                    <td className="px-4 py-3 fc-muted">{channel.type}</td>
                    <td className="px-4 py-3 fc-muted">{new Date(channel.created_at).toLocaleDateString(currentLocale())}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

function StatCard({ title, value, sub }: { title: string; value: number; sub?: string }) {
  useI18n();
  return (
    <div className="fc-admin-card">
      <div className="fc-muted text-sm mb-1">{title}</div>
      <div className="text-3xl font-bold">{value}</div>
      {sub && <div className="fc-muted text-sm mt-1">{sub}</div>}
    </div>
  )
}
