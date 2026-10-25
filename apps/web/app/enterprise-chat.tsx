"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import {
  listChatConversations, listChatMessages, listEmployees, openChatConversation, sendChatMessage,
  type ChatConversation, type ChatMessage, type Employee,
} from "../lib/api";

const errorText = (error: unknown) => error instanceof Error ? error.message : "暂时无法连接，请重试。";

export default function EnterpriseChat({ actor, tenant, initialPeer }: {
  actor: string; tenant: string; initialPeer: string | null;
}) {
  const [conversations, setConversations] = useState<ChatConversation[]>([]);
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [selected, setSelected] = useState<ChatConversation | null>(null);
  const [peer, setPeer] = useState("");
  const [moreEmployees, setMoreEmployees] = useState(false);
  const [moreConversations, setMoreConversations] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const alive = useRef(false);
  const name = (id: string) => employees.find((e) => e.user_id === id)?.display_name || `成员 ${id.slice(0, 8)}`;

  useEffect(() => {
    let active = true;
    let firstLoad = true;
    alive.current = true;
    async function load() {
      try {
        const [items, people] = await Promise.all([listChatConversations(actor, tenant), listEmployees(actor, tenant)]);
        if (!active) return;
        setConversations((old) => [...items, ...old.filter((row) => !items.some((item) => item.id === row.id))]);
        setEmployees((old) => [...people, ...old.filter((row) => !people.some((item) => item.user_id === row.user_id))]);
        if (firstLoad) { setMoreConversations(items.length === 50); setMoreEmployees(people.length === 100); firstLoad = false; }
        setNotice("");
      } catch (error) {
        if (active) { firstLoad = true; setConversations([]); setEmployees([]); setSelected(null); setNotice(errorText(error)); }
      }
    }
    void load().then(async () => {
      if (!active || !initialPeer) return;
      try {
        const item = await openChatConversation(actor, tenant, initialPeer);
        if (active) setSelected(item);
      } catch (error) { if (active) setNotice(errorText(error)); }
    });
    const timer = window.setInterval(() => { if (!document.hidden) void load(); }, 15000);
    return () => { active = false; alive.current = false; window.clearInterval(timer); };
  }, [actor, tenant, initialPeer]);

  async function open(peerId: string | null) {
    setBusy(true); setNotice(""); setSelected(null);
    try {
      const item = await openChatConversation(actor, tenant, peerId);
      if (!alive.current) return;
      setSelected(item);
      setConversations((rows) => [item, ...rows.filter((row) => row.id !== item.id)]);
    } catch (error) { if (alive.current) setNotice(errorText(error)); }
    finally { if (alive.current) setBusy(false); }
  }
  async function more(kind: "employees" | "conversations") {
    setBusy(true);
    try {
      if (kind === "employees") {
        const rows = await listEmployees(actor, tenant, employees.length);
        if (alive.current) { setEmployees((old) => [...old, ...rows]); setMoreEmployees(rows.length === 100); }
      } else {
        const rows = await listChatConversations(actor, tenant, conversations.length);
        if (alive.current) { setConversations((old) => [...old, ...rows.filter((row) => !old.some((v) => v.id === row.id))]); setMoreConversations(rows.length === 50); }
      }
    } catch (error) { if (alive.current) { setSelected(null); setNotice(errorText(error)); } }
    finally { if (alive.current) setBusy(false); }
  }

  return <section className="enterprise-chat">
    <div className="page-heading"><div><p className="kicker">企业协作 · 第三阶段</p><h1>企业聊天</h1>
      <p>消息加密保存，公共聊天室面向本企业有效成员；私聊仅双方可读，不赋予任何文档权限。</p></div></div>
    {notice && <p className="form-error" role="alert">{notice}</p>}
    <div className="chat-layout">
      <aside className="chat-sidebar" aria-label="聊天会话">
        <button className="primary-button wide" disabled={busy} onClick={() => void open(null)}>企业公共聊天室</button>
        <form className="dialog-form" onSubmit={(event) => { event.preventDefault(); if (peer) void open(peer); }}>
          <label>选择同事私聊<select value={peer} onChange={(event) => setPeer(event.target.value)} disabled={busy}>
            <option value="">请选择有效成员</option>{employees.filter((e) => e.active && e.user_id !== actor).map((e) => <option key={e.user_id} value={e.user_id}>{name(e.user_id)} · {e.department || "未设置部门"} · {e.user_id.slice(0, 8)}</option>)}
          </select></label>
          <button className="secondary-button" disabled={busy || !peer}>开始私聊</button>
        </form>
        {moreEmployees && <button disabled={busy} onClick={() => void more("employees")}>加载更多员工</button>}
        <h2>已有会话</h2><p className="upload-hint">每 15 秒更新列表，不提供在线状态或已读回执。</p>
        {conversations.map((item) => <button key={item.id} className={`employee-item ${selected?.id === item.id ? "selected" : ""}`}
          disabled={busy} onClick={() => setSelected(item)}><strong>{item.kind === "lobby" ? "企业公共聊天室" : name(item.peer_user_id!)}</strong>
          <small>{item.kind === "lobby" ? "本企业成员可见历史记录" : "双方私聊 · 无管理员旁观权限"}</small></button>)}
        {moreConversations && <button disabled={busy} onClick={() => void more("conversations")}>加载更多会话</button>}
      </aside>
      {selected ? <ChatThread key={`${actor}:${tenant}:${selected.id}`} actor={actor} tenant={tenant} conversation={selected} name={name} />
        : <section className="chat-thread empty-state"><h2>选择一个会话</h2><p>可进入公共聊天室，或从员工中心选择同事发起私聊。</p><p>请勿粘贴未经接收者授权的机密资料。当前仅支持纯文本，不支持附件或自动转发文档。</p></section>}
    </div>
  </section>;
}

function ChatThread({ actor, tenant, conversation, name }: {
  actor: string; tenant: string; conversation: ChatConversation; name: (id: string) => string;
}) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [canSend, setCanSend] = useState(false);
  const [older, setOlder] = useState(false);
  const [loading, setLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [draft, setDraft] = useState("");
  const [notice, setNotice] = useState("");
  const [sendNotice, setSendNotice] = useState("");
  const cursor = useRef<number | null>(null);
  const first = useRef<number | null>(null);
  const alive = useRef(false);
  const readBusy = useRef(false);
  const sendBusy = useRef(false);
  const retry = useRef<{ body: string; id: string } | null>(null);
  const history = useRef<HTMLDivElement>(null);

  const load = useCallback(async (previous = false) => {
    if (readBusy.current || !alive.current) return;
    readBusy.current = true; setLoading(true);
    const initial = cursor.current === null;
    const nearBottom = history.current ? history.current.scrollHeight - history.current.scrollTop - history.current.clientHeight < 100 : true;
    try {
      const page = await listChatMessages(actor, tenant, conversation.id,
        previous && first.current !== null ? { before: first.current } : initial ? {} : { after: cursor.current! });
      if (!alive.current) return;
      setCanSend(page.conversation.can_send); setNotice("");
      if (initial || previous) setOlder(page.has_more);
      if (page.messages.length) {
        first.current = Math.min(first.current ?? Infinity, page.messages[0].sequence);
        cursor.current = Math.max(cursor.current ?? 0, page.messages[page.messages.length - 1].sequence);
        setMessages((old) => [...old, ...page.messages.filter((row) => !old.some((item) => item.id === row.id))].sort((a, b) => a.sequence - b.sequence));
        if (!previous && (initial || nearBottom)) window.requestAnimationFrame(() => { if (alive.current && history.current) history.current.scrollTop = history.current.scrollHeight; });
      } else if (initial) cursor.current = 0;
    } catch (error) {
      if (alive.current) {
        // Fail closed: do not leave old messages visible after revoked access or a failed refresh.
        setMessages([]); setCanSend(false); setOlder(false); cursor.current = null; first.current = null;
        setNotice(errorText(error));
      }
    } finally { readBusy.current = false; if (alive.current) setLoading(false); }
  }, [actor, tenant, conversation.id]);

  useEffect(() => {
    alive.current = true;
    void load();
    const timer = window.setInterval(() => { if (!document.hidden) void load(); }, 5000);
    const onVisible = () => { if (!document.hidden) void load(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => { alive.current = false; window.clearInterval(timer); document.removeEventListener("visibilitychange", onVisible); };
  }, [load]);

  async function send(event: FormEvent) {
    event.preventDefault();
    const body = draft.trim();
    if (!body || sendBusy.current || !canSend) return;
    sendBusy.current = true; setSending(true); setSendNotice("");
    if (!retry.current || retry.current.body !== body) retry.current = { body, id: crypto.randomUUID() };
    try {
      await sendChatMessage(actor, tenant, conversation.id, retry.current.id, body);
      if (!alive.current) return;
      retry.current = null; setDraft(""); setSendNotice("消息已发送。");
      await load();
    } catch (error) {
      if (alive.current) { setSendNotice(`${errorText(error)} 内容已保留，可重试；请勿重复改写发送。`); await load(); }
    } finally { sendBusy.current = false; if (alive.current) setSending(false); }
  }

  return <section className="chat-thread" aria-label="聊天内容">
    <header><h2>{conversation.kind === "lobby" ? "企业公共聊天室" : `与 ${name(conversation.peer_user_id!)} 私聊`}</h2>
      <p>{conversation.kind === "lobby" ? "本企业当前及后续加入的有效成员均可查看历史消息，请勿发送机密资料。" : "仅双方的有效企业账号可读取；这不是端到端加密。"}</p>
      <button className="secondary-button" disabled={loading} onClick={() => void load()}>刷新消息</button></header>
    {notice && <p role="alert" className="form-error">{notice} 已隐藏消息，连接恢复后重新校验权限。</p>}
    <div className="chat-history" ref={history} aria-label="消息历史">
      {older && <button disabled={loading} onClick={() => void load(true)}>加载更早消息</button>}
      {!messages.length && !notice && <p className="upload-hint">{loading ? "正在加载消息…" : "暂无消息，开始交流吧。"}</p>}
      {messages.map((message) => <article className={`chat-message ${message.sender_id === actor ? "own" : ""}`} key={message.id}>
        <div><strong>{message.sender_id === actor ? "我" : name(message.sender_id)}</strong><time dateTime={message.created_at}>{new Date(message.created_at).toLocaleString("zh-CN")}</time></div>
        <p>{message.body}</p>
      </article>)}
    </div>
    {!canSend && !notice && <p className="form-notice">正在校验权限，或对方已停用；暂不可发送。</p>}
    <form className="chat-composer" onSubmit={send}>
      <label>消息内容<textarea aria-label="消息内容" value={draft} maxLength={4000} disabled={sending || !canSend} onChange={(event) => { setDraft(event.target.value); setSendNotice(""); }} placeholder="输入消息；回车换行，点击按钮发送" /></label>
      <div><small>{draft.length}/4000 · 纯文本 · 不进入 AI 检索</small><button className="primary-button" disabled={sending || !canSend || !draft.trim()}>{sending ? "正在发送…" : "发送消息"}</button></div>
      {sendNotice && <p role="status" className="form-notice">{sendNotice}</p>}
    </form>
  </section>;
}
