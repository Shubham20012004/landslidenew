// Server-Sent Events client with bounded exponential back-off and resynchronisation.
export class LiveStream {
  constructor(url, handlers) {
    this.url = url;
    this.h = handlers;          // { snapshot, reading, event, model, heartbeat, status }
    this.es = null;
    this.retry = 1000;
    this.timer = null;
    this.watchdog = null;
    this.lastMessage = 0;
  }

  start() {
    this.connect();
    // Heartbeats arrive every 5 s; 15 s of silence means the connection is dead.
    this.watchdog = setInterval(() => {
      if (this.es && this.lastMessage && Date.now() - this.lastMessage > 15000) this.reconnect("timeout");
    }, 3000);
  }

  connect() {
    clearTimeout(this.timer);
    this.h.status?.("connecting");
    const es = new EventSource(this.url);
    this.es = es;
    const on = (type, fn) => es.addEventListener(type, (e) => {
      this.lastMessage = Date.now();
      let data;
      try { data = JSON.parse(e.data); } catch { return; }
      fn?.(data);
    });
    on("snapshot", (d) => { this.retry = 1000; this.h.status?.("live"); this.h.snapshot?.(d); });
    on("reading", this.h.reading);
    on("event", this.h.event);
    on("model", this.h.model);
    on("heartbeat", this.h.heartbeat);
    es.onerror = () => this.reconnect("error");
  }

  reconnect(reason) {
    if (this.es) { this.es.close(); this.es = null; }
    this.lastMessage = 0;
    this.h.status?.("down", reason);
    clearTimeout(this.timer);
    const delay = this.retry;
    this.retry = Math.min(this.retry * 2, 30000);
    this.timer = setTimeout(() => this.connect(), delay);
  }
}
