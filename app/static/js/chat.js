// All browser-side logic. No frameworks: just fetch() for REST and WebSocket for real-time.
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const SESSION_KEY = "chat_session"; // sessionStorage = separate for every tab/incognito window

  const state = {
    me: null,            // { id, username, token }
    users: [],           // other users [{id, username, is_online}]
    searchResults: [],
    searchQuery: "",
    searching: false,
    searchError: "",
    selectedId: null,    // user we are chatting with
    selectedUser: null,
    ws: null,
    attempts: 0,
    reconnectTimer: null,
    loggedOut: false,
    unread: {},          // { userId: count }
    shownIds: new Set(), // message ids already drawn (prevents duplicates)
    selectedAttachment: null,
    uploadRequest: null,
    sending: false,
    objectUrls: new Set(),
  };

  // ---------- small helpers ----------
  function toast(text) {
    const t = $("toast");
    t.textContent = text;
    t.classList.remove("hidden");
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => t.classList.add("hidden"), 3500);
  }

  async function api(path, options = {}) {
    const headers = { ...(options.headers || {}) };
    if (!(options.body instanceof FormData)) headers["Content-Type"] = "application/json";
    if (state.me) headers.Authorization = `Bearer ${state.me.token}`;
    const res = await fetch(path, { ...options, headers });
    let data = null;
    try { data = await res.json(); } catch (_) { /* no body */ }
    if (!res.ok) {
      let detail = data && data.detail;
      if (Array.isArray(detail)) detail = (detail[0].msg || "Invalid input").replace(/^Value error, /, "");
      const err = new Error(detail || `Request failed (${res.status})`);
      err.status = res.status;
      throw err;
    }
    return data;
  }

  function formatTime(iso) {
    const d = new Date(iso);
    const time = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    return d.toDateString() === new Date().toDateString()
      ? time
      : d.toLocaleDateString([], { day: "numeric", month: "short" }) + ", " + time;
  }

  const saveSession = () => sessionStorage.setItem(SESSION_KEY, JSON.stringify(state.me));
  const loadSession = () => { try { return JSON.parse(sessionStorage.getItem(SESSION_KEY)); } catch (_) { return null; } };

  // ---------- screens ----------
  function showLogin(message) {
    $("app").classList.add("hidden");
    $("login-screen").classList.remove("hidden");
    $("login-btn").disabled = false;
    $("login-btn").textContent = "Join chat";
    const err = $("login-error");
    err.textContent = message || "";
    err.classList.toggle("hidden", !message);
    $("username-input").focus();
  }

  function showApp() {
    $("login-screen").classList.add("hidden");
    $("app").classList.remove("hidden");
    $("me-name").textContent = state.me.username;
  }

  function setConn(status) {
    const b = $("conn-banner");
    b.className = "banner";
    if (status === "online") { b.classList.add("hidden"); }
    else if (status === "connecting") { b.classList.add("connecting"); b.textContent = "Connecting…"; }
    else { b.classList.add("offline"); b.textContent = status === "replaced"
      ? "This account was opened in another window. Close this one or reload."
      : "Connection lost. Reconnecting…"; }
    $("send-btn").disabled = status !== "online" || state.sending;
  }

  // ---------- join / logout ----------
  async function join(username) {
    const body = { username };
    const saved = loadSession();
    if (saved && saved.username.toLowerCase() === username.toLowerCase()) body.token = saved.token;

    state.me = null; // no auth header while joining
    const data = await api("/users", { method: "POST", body: JSON.stringify(body) });
    state.me = { id: data.user.id, username: data.user.username, token: data.token };
    saveSession();
    state.loggedOut = false;
    showApp();
    connectWS();
    await loadUsers();
  }

  function logout(message) {
    state.loggedOut = true;
    clearTimeout(state.reconnectTimer);
    if (state.ws) state.ws.close();
    clearSelectedAttachment();
    clearAttachmentObjectUrls();
    state.sending = false;
    setComposerBusy(false);
    sessionStorage.removeItem(SESSION_KEY);
    Object.assign(state, {
      me: null, users: [], searchResults: [], searchQuery: "", searching: false,
      searchError: "", selectedId: null, selectedUser: null, unread: {}, ws: null,
    });
    $("user-search").value = "";
    $("sidebar-title").textContent = "Chats";
    $("app").classList.remove("chat-open");
    $("empty-state").classList.remove("hidden");
    $("chat-area").classList.add("hidden");
    showLogin(message);
  }

  // ---------- WebSocket ----------
  function connectWS() {
    clearTimeout(state.reconnectTimer);
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const url = `${proto}://${location.host}/ws/${state.me.id}?token=${encodeURIComponent(state.me.token)}`;
    const ws = new WebSocket(url);
    state.ws = ws;
    setConn("connecting");

    ws.onopen = () => {
      state.attempts = 0;
      setConn("online");
      loadUsers();
      if (state.selectedId) loadHistory(state.selectedId); // catch up on anything missed while offline
    };

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.type === "presence") loadUsers();
      else if (data.type === "error") toast(data.detail);
      else if (data.type === "message") handleIncoming(data.message);
    };

    ws.onclose = (event) => {
      if (state.ws !== ws || state.loggedOut) return;
      if (event.code === 4401) return logout("Your session is no longer valid. Please join again.");
      if (event.code === 4000) return setConn("replaced");
      setConn("offline");
      const delay = Math.min(1000 * 2 ** state.attempts++, 10000); // 1s, 2s, 4s ... max 10s
      state.reconnectTimer = setTimeout(connectWS, delay);
    };
  }

  function handleIncoming(m) {
    const otherId = m.sender_id === state.me.id ? m.receiver_id : m.sender_id;
    const peer = state.users.find((user) => user.id === otherId);
    if (peer && m.id > (peer.last_message_id || 0)) peer.last_message_id = m.id;
    if (otherId === state.selectedId) {
      appendMessage(m);
      if (m.sender_id !== state.me.id) markRead(otherId);
      else loadUsers();
    } else {
      state.unread[otherId] = (state.unread[otherId] || 0) + 1;
      renderUsers();
      loadUsers();
    }
  }

  // ---------- users ----------
  async function loadUsers() {
    if (!state.me) return;
    try {
      const previousMessageIds = new Map(
        state.users.map((user) => [user.id, user.last_message_id || 0]),
      );
      state.users = (await api("/users")).map((user) => ({
        ...user,
        last_message_id: Math.max(
          user.last_message_id || 0,
          previousMessageIds.get(user.id) || 0,
        ) || null,
      }));
      state.unread = Object.fromEntries(state.users.map((user) => [user.id, user.unread_count]));
      renderUsers();
      renderHeader();
    } catch (err) {
      if (err.status === 401) logout("Your session expired. Please join again.");
    }
  }

  function renderUsers() {
    const list = $("user-list");
    const searching = Boolean(state.searchQuery);
    const displayedUsers = searching ? state.searchResults : state.users;
    list.innerHTML = "";
    $("sidebar-title").textContent = searching ? "Search results" : "Chats";
    if (searching && displayedUsers.length === 0) {
      const li = document.createElement("li");
      li.className = "list-empty";
      li.textContent = state.searchError
        || (state.searching ? "Searching users…" : "No matching users found.");
      list.appendChild(li);
      return;
    }
    if (!searching && displayedUsers.length === 0) {
      const li = document.createElement("li");
      li.className = "list-empty";
      li.textContent = "No chats yet. Search for a user to start a conversation.";
      list.appendChild(li);
      return;
    }
    const sorted = [...state.users].sort((a, b) =>
      ((b.last_message_id || 0) - (a.last_message_id || 0))
      || (b.is_online - a.is_online)
      || a.username.localeCompare(b.username));
    const usersToRender = searching
      ? [...displayedUsers].sort((a, b) => a.username.localeCompare(b.username))
      : sorted;
    for (const u of usersToRender) {
      const li = document.createElement("li");
      li.className = "user-item" + (u.id === state.selectedId ? " active" : "");
      li.addEventListener("click", () => selectUser(u.id));

      const avatar = document.createElement("div");
      avatar.className = "avatar";
      avatar.textContent = u.username[0].toUpperCase();

      const info = document.createElement("div");
      info.className = "user-info";
      const name = document.createElement("div");
      name.className = "user-name";
      name.textContent = u.username;
      const status = document.createElement("div");
      status.className = "status";
      const dot = document.createElement("span");
      dot.className = "dot" + (u.is_online ? " online" : "");
      status.append(dot, u.is_online ? "Online" : "Offline");
      info.append(name, status);

      li.append(avatar, info);
      const unreadCount = searching ? 0 : state.unread[u.id] || 0;
      if (unreadCount > 0) {
        const badge = document.createElement("span");
        badge.className = "badge";
        badge.textContent = unreadCount > 99 ? "99+" : unreadCount;
        badge.setAttribute("aria-label", `${unreadCount} unread message${unreadCount === 1 ? "" : "s"}`);
        li.appendChild(badge);
      }
      list.appendChild(li);
    }
  }

  async function searchUsers(query) {
    state.searching = true;
    state.searchError = "";
    renderUsers();
    try {
      const results = await api(`/users/search?q=${encodeURIComponent(query)}`);
      if (query !== state.searchQuery) return;
      state.searchResults = results;
    } catch (error) {
      if (query !== state.searchQuery) return;
      if (error.status === 401) {
        logout("Your session expired. Please join again.");
        return;
      }
      state.searchError = "Could not search users: " + error.message;
    } finally {
      if (query === state.searchQuery) {
        state.searching = false;
        renderUsers();
      }
    }
  }

  function renderHeader() {
    const u = state.users.find((x) => x.id === state.selectedId)
      || state.searchResults.find((x) => x.id === state.selectedId)
      || (state.selectedUser && state.selectedUser.id === state.selectedId ? state.selectedUser : null);
    if (!u) return;
    $("peer-avatar").textContent = u.username[0].toUpperCase();
    $("peer-name").textContent = u.username;
    const s = $("peer-status");
    s.textContent = u.is_online ? "● Online" : "Offline";
    s.className = "peer-status" + (u.is_online ? " online" : "");
  }

  function clearAttachmentObjectUrls() {
    for (const url of state.objectUrls) URL.revokeObjectURL(url);
    state.objectUrls.clear();
    closeImageLightbox();
  }

  function formatFileSize(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  async function getAttachmentBlob(id) {
    const response = await fetch(`/attachments/${id}`, {
      headers: { Authorization: `Bearer ${state.me.token}` },
    });
    if (!response.ok) {
      let detail = "";
      try {
        const data = await response.json();
        detail = data.detail || "";
      } catch (_) { /* use the status-based message below */ }
      const error = new Error(detail || `Attachment request failed (${response.status})`);
      error.status = response.status;
      throw error;
    }
    return response.blob();
  }

  async function downloadAttachment(attachment, button) {
    button.disabled = true;
    const originalText = button.textContent;
    button.textContent = "…";
    try {
      const blob = await getAttachmentBlob(attachment.id);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = attachment.display_name;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
      if (error.status === 401) logout("Your session expired. Please join again.");
      else toast("Could not download attachment: " + error.message);
    } finally {
      button.disabled = false;
      button.textContent = originalText;
    }
  }

  function openImageLightbox(url, name) {
    $("lightbox-image").src = url;
    $("lightbox-image").alt = name;
    $("image-lightbox").classList.remove("hidden");
    $("lightbox-close").focus();
  }

  function closeImageLightbox() {
    const lightbox = $("image-lightbox");
    if (!lightbox) return;
    lightbox.classList.add("hidden");
    $("lightbox-image").removeAttribute("src");
    $("lightbox-image").alt = "";
  }

  // ---------- conversation ----------
  async function selectUser(id) {
    if (state.selectedId !== id && !state.sending) clearSelectedAttachment();
    state.selectedId = id;
    state.selectedUser = state.searchResults.find((user) => user.id === id)
      || state.users.find((user) => user.id === id)
      || state.selectedUser;
    state.unread[id] = 0;
    $("empty-state").classList.add("hidden");
    $("chat-area").classList.remove("hidden");
    $("app").classList.add("chat-open");
    renderUsers();
    renderHeader();
    await loadHistory(id);
    $("message-input").focus();
  }

  async function loadHistory(id) {
    const box = $("messages");
    clearAttachmentObjectUrls();
    state.shownIds = new Set();
    box.innerHTML = '<div class="state-msg">Loading messages…</div>';
    try {
      const history = await api(`/messages/${id}`);
      if (state.selectedId !== id) return; // user clicked someone else meanwhile
      state.unread[id] = 0;
      renderUsers();
      box.innerHTML = "";
      if (history.length === 0) box.innerHTML = '<div class="state-msg">No messages yet. Say hi 👋</div>';
      history.forEach(appendMessage);
    } catch (err) {
      box.innerHTML = "";
      const d = document.createElement("div");
      d.className = "state-msg";
      d.textContent = "Could not load messages: " + err.message;
      box.appendChild(d);
      await loadUsers();
    }
  }

  function appendMessage(m) {
    if (state.shownIds.has(m.id)) return;
    state.shownIds.add(m.id);
    const box = $("messages");
    const placeholder = box.querySelector(".state-msg");
    if (placeholder) placeholder.remove();

    const mine = m.sender_id === state.me.id;
    const row = document.createElement("div");
    row.className = "msg-row " + (mine ? "sent" : "received");
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    if (m.content) {
      const text = document.createElement("div");
      text.textContent = m.content;
      bubble.appendChild(text);
    }
    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = formatTime(m.created_at);

    if (m.attachment) {
      const attachment = m.attachment;
      if (attachment.kind === "image") {
        const image = document.createElement("img");
        image.className = "message-image";
        image.alt = attachment.display_name;
        image.setAttribute("aria-label", `Open image ${attachment.display_name}`);
        image.addEventListener("click", () => {
          if (image.dataset.objectUrl) openImageLightbox(image.dataset.objectUrl, attachment.display_name);
        });
        bubble.appendChild(image);
        getAttachmentBlob(attachment.id).then((blob) => {
          if (!image.isConnected) return;
          const url = URL.createObjectURL(blob);
          state.objectUrls.add(url);
          image.dataset.objectUrl = url;
          image.src = url;
        }).catch((error) => {
          if (error.status === 401) logout("Your session expired. Please join again.");
          if (image.isConnected) {
            image.alt = `Could not load image: ${attachment.display_name}`;
            image.classList.add("attachment-load-error");
            const unavailable = document.createElement("span");
            unavailable.className = "attachment-load-error";
            unavailable.textContent = "Image unavailable";
            bubble.insertBefore(unavailable, meta);
          }
        });
      } else {
        const card = document.createElement("div");
        card.className = "file-attachment";
        const icon = document.createElement("span");
        icon.className = "file-attachment-icon";
        icon.textContent = "📄";
        const details = document.createElement("div");
        details.className = "file-attachment-details";
        const name = document.createElement("span");
        name.className = "file-attachment-name";
        name.textContent = attachment.display_name;
        const type = document.createElement("span");
        type.className = "file-attachment-type";
        const extension = attachment.display_name.includes(".")
          ? attachment.display_name.split(".").pop().toUpperCase()
          : attachment.media_type;
        type.textContent = `${extension} · ${formatFileSize(attachment.size)}`;
        const download = document.createElement("button");
        download.className = "file-download";
        download.type = "button";
        download.textContent = "↓";
        download.setAttribute("aria-label", `Download ${attachment.display_name}`);
        download.addEventListener("click", () => downloadAttachment(attachment, download));
        details.append(name, type);
        card.append(icon, details, download);
        bubble.appendChild(card);
      }
    }

    bubble.appendChild(meta);
    row.appendChild(bubble);
    box.appendChild(row);
    box.scrollTop = box.scrollHeight;
  }

  async function markRead(id) {
    state.unread[id] = 0;
    renderUsers();
    try {
      await api(`/messages/${id}/read`, { method: "POST" });
      await loadUsers();
    } catch (err) {
      if (err.status === 401) logout("Your session expired. Please join again.");
      else {
        await loadUsers();
        toast("Could not update read status: " + err.message);
      }
    }
  }

  function setComposerBusy(busy) {
    state.sending = busy;
    $("send-btn").disabled = busy || !state.ws || state.ws.readyState !== WebSocket.OPEN;
    $("message-input").disabled = busy;
    ["emoji-toggle", "image-attach-btn", "file-attach-btn"].forEach((id) => {
      $(id).disabled = busy;
    });
  }

  function clearSelectedAttachment() {
    if (state.uploadRequest) {
      const request = state.uploadRequest;
      state.uploadRequest = null;
      request.abort();
    }
    if (state.selectedAttachment && state.selectedAttachment.previewUrl) {
      URL.revokeObjectURL(state.selectedAttachment.previewUrl);
    }
    state.selectedAttachment = null;
    $("image-input").value = "";
    $("file-input").value = "";
    $("attachment-preview-image").removeAttribute("src");
    $("attachment-preview-image").classList.add("hidden");
    $("composer-preview").classList.add("hidden");
    $("upload-progress").classList.add("hidden");
    $("upload-progress").value = 0;
    $("upload-progress-label").textContent = "";
    if (!state.sending) setComposerBusy(false);
  }

  function selectAttachment(file, kind) {
    const extension = file.name.includes(".") ? file.name.split(".").pop().toLowerCase() : "";
    const allowed = kind === "image"
      ? ["jpg", "jpeg", "png", "gif", "webp"]
      : ["pdf", "txt", "doc", "docx", "xls", "xlsx", "csv"];
    const maxBytes = Number($("chat-area").dataset[kind === "image" ? "imageMaxBytes" : "fileMaxBytes"]);
    if (!allowed.includes(extension)) {
      toast(kind === "image" ? "Choose a JPEG, PNG, GIF, or WebP image." : "Choose a supported document file.");
      return;
    }
    if (file.size > maxBytes) {
      toast(`File exceeds the ${formatFileSize(maxBytes)} upload limit.`);
      return;
    }
    clearSelectedAttachment();
    const previewUrl = kind === "image" ? URL.createObjectURL(file) : null;
    state.selectedAttachment = { file, kind, previewUrl };
    $("attachment-preview-name").textContent = file.name;
    $("attachment-preview-size").textContent = formatFileSize(file.size);
    if (previewUrl) {
      $("attachment-preview-image").src = previewUrl;
      $("attachment-preview-image").classList.remove("hidden");
    }
    $("composer-preview").classList.remove("hidden");
    $("message-input").focus();
  }

  function uploadAttachment(file, receiverId, caption) {
    return new Promise((resolve, reject) => {
      const request = new XMLHttpRequest();
      const form = new FormData();
      form.append("file", file, file.name);
      form.append("caption", caption);
      state.uploadRequest = request;
      request.open("POST", `/attachments/${receiverId}`);
      request.setRequestHeader("Authorization", `Bearer ${state.me.token}`);
      request.upload.addEventListener("progress", (event) => {
        if (!event.lengthComputable) return;
        const progress = $("upload-progress");
        progress.value = Math.round((event.loaded / event.total) * 100);
        progress.classList.remove("hidden");
        $("upload-progress-label").textContent = `Uploading ${progress.value}%`;
      });
      request.addEventListener("load", () => {
        if (state.uploadRequest === request) state.uploadRequest = null;
        let data;
        try {
          data = JSON.parse(request.responseText);
        } catch (_) {
          reject(new Error("The server returned an invalid upload response."));
          return;
        }
        if (request.status < 200 || request.status >= 300) {
          let detail = data && data.detail;
          if (Array.isArray(detail)) detail = detail[0].msg || "Invalid upload";
          const error = new Error(detail || `Upload failed (${request.status})`);
          error.status = request.status;
          reject(error);
          return;
        }
        resolve(data);
      });
      request.addEventListener("error", () => {
        if (state.uploadRequest === request) state.uploadRequest = null;
        reject(new Error("Upload failed because of a network error. Check your connection and try again."));
      });
      request.addEventListener("abort", () => {
        if (state.uploadRequest === request) state.uploadRequest = null;
        const error = new Error("Upload canceled.");
        error.aborted = true;
        reject(error);
      });
      request.send(form);
    });
  }

  function insertEmoji(emoji) {
    const input = $("message-input");
    const start = input.selectionStart ?? input.value.length;
    const end = input.selectionEnd ?? start;
    if (input.value.length - (end - start) + emoji.length > 2000) {
      toast("Message limit is 2000 characters.");
      return;
    }
    input.setRangeText(emoji, start, end, "end");
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.focus();
  }

  // ---------- events ----------
  $("login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = $("username-input").value.trim();
    if (!name) return;
    $("login-btn").disabled = true;
    $("login-btn").textContent = "Joining…";
    try {
      await join(name);
    } catch (err) {
      showLogin(err.message);
    }
  });

  $("message-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = $("message-input");
    const content = input.value.trim();
    if (!state.selectedId || state.sending) return;
    if (!state.ws || state.ws.readyState !== WebSocket.OPEN) return toast("Not connected yet. Please wait…");
    if (state.selectedAttachment) {
      setComposerBusy(true);
      try {
        const message = await uploadAttachment(
          state.selectedAttachment.file,
          state.selectedId,
          content,
        );
        handleIncoming(message);
        clearSelectedAttachment();
        input.value = "";
      } catch (error) {
        if (error.status === 401) logout("Your session expired. Please join again.");
        else if (!error.aborted) toast(error.message);
      } finally {
        setComposerBusy(false);
      }
      return;
    }
    if (!content) return;
    // We send ONLY who it's for and what it says. The server decides who the sender is.
    state.ws.send(JSON.stringify({ receiver_id: state.selectedId, content }));
    input.value = "";
  });

  $("emoji-toggle").addEventListener("click", () => {
    const popover = $("emoji-picker-popover");
    const open = popover.classList.contains("hidden");
    popover.classList.toggle("hidden", !open);
    $("emoji-toggle").setAttribute("aria-expanded", String(open));
  });

  $("emoji-picker").addEventListener("emoji-click", (event) => {
    const emoji = event.detail && event.detail.unicode;
    if (emoji) insertEmoji(emoji);
  });

  $("image-attach-btn").addEventListener("click", () => $("image-input").click());
  $("file-attach-btn").addEventListener("click", () => $("file-input").click());
  $("image-input").addEventListener("change", (event) => {
    const input = event.currentTarget;
    const file = input.files[0];
    input.value = "";
    if (file) selectAttachment(file, "image");
  });
  $("file-input").addEventListener("change", (event) => {
    const input = event.currentTarget;
    const file = input.files[0];
    input.value = "";
    if (file) selectAttachment(file, "file");
  });
  $("cancel-attachment-btn").addEventListener("click", clearSelectedAttachment);

  document.addEventListener("click", (event) => {
    const picker = $("emoji-picker-popover");
    if (!picker.classList.contains("hidden")
      && !picker.contains(event.target)
      && !$("emoji-toggle").contains(event.target)) {
      picker.classList.add("hidden");
      $("emoji-toggle").setAttribute("aria-expanded", "false");
    }
    if (event.target === $("image-lightbox")) closeImageLightbox();
  });
  $("lightbox-close").addEventListener("click", closeImageLightbox);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      $("emoji-picker-popover").classList.add("hidden");
      $("emoji-toggle").setAttribute("aria-expanded", "false");
      closeImageLightbox();
    }
  });

  $("logout-btn").addEventListener("click", () => logout());
  $("user-search").addEventListener("input", (event) => {
    const query = event.currentTarget.value.trim();
    state.searchQuery = query;
    state.searchResults = [];
    state.searchError = "";
    if (query) searchUsers(query);
    else {
      state.searching = false;
      renderUsers();
    }
  });
  $("back-btn").addEventListener("click", () => {
    state.selectedId = null;
    state.selectedUser = null;
    if (!state.sending) clearSelectedAttachment();
    $("emoji-picker-popover").classList.add("hidden");
    $("emoji-toggle").setAttribute("aria-expanded", "false");
    $("app").classList.remove("chat-open");
    $("chat-area").classList.add("hidden");
    $("empty-state").classList.remove("hidden");
    renderUsers();
  });

  // ---------- start ----------
  (async function init() {
    const saved = loadSession();
    if (!saved) return showLogin();
    try {
      await join(saved.username); // resume after a page refresh
    } catch (_) {
      showLogin();
    }
  })();

  // ---------- Dark Mode ----------
  const darkModeButton = document.getElementById("theme-toggle");

  function applySavedTheme() {
    const savedTheme = localStorage.getItem("chat-theme");
    const isDark = savedTheme === "dark";

    document.body.classList.toggle("dark-mode", isDark);

    if (darkModeButton) {
      darkModeButton.textContent = isDark ? "☀️ Light Mode" : "🌙 Dark Mode";
      darkModeButton.setAttribute("aria-label", isDark ? "Switch to light mode" : "Switch to dark mode");
    }
  }

  applySavedTheme();

  if (darkModeButton) {
    darkModeButton.addEventListener("click", () => {
      const isDark = document.body.classList.toggle("dark-mode");
      localStorage.setItem("chat-theme", isDark ? "dark" : "light");
      darkModeButton.textContent = isDark ? "☀️ Light Mode" : "🌙 Dark Mode";
      darkModeButton.setAttribute("aria-label", isDark ? "Switch to light mode" : "Switch to dark mode");
    });
  }

})();
