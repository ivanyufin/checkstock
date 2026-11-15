/* CheckStock manager UI. Stock calculations stay on the FastAPI backend. */
(() => {
  "use strict";

  const $ = (selector) => document.querySelector(selector);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("ru-RU");
  const state = { inventory: {items: [], totals: {}, warehouses: []}, warehouses: [], transfers: [], fbs: [], shipments: [], movements: [], currentOperation: null, requestKey: null };
  const titles = { overview: "Обзор", inventory: "Остатки и товары", warehouses: "Склады", transfers: "Перемещения", fbs: "Заказы FBS", fbo: "Отправки FBO", history: "История" };
  const kinds = {receipt:"Приход", transfer_sent:"Отправка ФФ", transfer_received:"Приёмка ФФ", transfer_cancelled:"Отмена перемещения", fbs_reserved:"Резерв FBS", fbs_cancelled:"Отмена FBS", fbs_shipped:"Отправка FBS", fbo_shipped:"Отправка FBO", fbo_received:"Приёмка FBO"};
  const statuses = {in_transit:"В пути",received:"Принято",active:"В резерве",cancelled:"Отменено",shipped:"Отправлено"};
  let modal;

  async function request(path, options = {}) {
    let response;
    try { response = await fetch(path, { cache: "no-store", ...options }); }
    catch (_) { throw new Error("Нет соединения с сервером. Проверьте работу CheckStock."); }
    let body = {};
    try { body = await response.json(); } catch (_) { /* unexpected response body */ }
    if (!response.ok) {
      let detail = body.detail;
      if (Array.isArray(detail)) detail = detail.map((item) => item.msg || "Ошибка в форме").join("; ");
      throw new Error(typeof detail === "string" ? detail : `Ошибка сервера: ${response.status}`);
    }
    return body;
  }

  function putRows(selector, rows, emptyMessage = "Нет записей") {
    const tbody = $(selector);
    tbody.innerHTML = rows.length ? rows.join("") : `<tr><td colspan="10" class="empty">${esc(emptyMessage)}</td></tr>`;
  }
  function product(id) { return state.inventory.items.find((p) => p.id === id); }
  function productLabel(id) { const p = product(id); return p ? p.sku : `#${id}`; }
  function whLabel(id) { return state.warehouses.find((w) => w.id === id)?.name || `#${id}`; }
  function statusPill(status) { return `<span class="status-pill status-${esc(status)}">${esc(statuses[status] || status)}</span>`; }
  function asDate(iso) { if (!iso) return "—"; const date = new Date(iso); return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString("ru-RU", {dateStyle:"short", timeStyle:"short"}); }
  function buttons(action, id, label, style = "outline-primary") { return `<button type="button" class="btn btn-sm btn-${style}" data-process="${esc(action)}" data-id="${Number(id)}">${esc(label)}</button>`; }
  function showError(message) { const element = $("#global-error"); element.textContent = message; element.classList.remove("d-none"); }
  function clearError() { $("#global-error").classList.add("d-none"); }
  function toast(message) { $("#toast-message").textContent = message; bootstrap.Toast.getOrCreateInstance($("#app-toast"), {delay:3200}).show(); }

  function refreshUI() {
    const t = state.inventory.totals;
    const transit = (t.transit_between_warehouses || 0) + (t.transit_to_customer_fbs || 0) + (t.transit_to_marketplace_fbo || 0);
    [["#total-on-hand", t.on_hand],["#total-available", t.available],["#total-reserved", t.reserved],["#total-transit", transit],["#transit-ff",t.transit_between_warehouses],["#transit-fbs",t.transit_to_customer_fbs],["#transit-fbo",t.transit_to_marketplace_fbo],["#received-fbo",t.received_on_marketplace_fbo]].forEach(([id,n]) => { $(id).textContent = fmt(n); });

    const allStock = state.inventory.items.flatMap((item) => item.warehouses.map((wh) => ({item, wh})));
    putRows("#overview-inventory", allStock.slice(0, 8).map(({item,wh}) => `<tr><td class="sku">${esc(item.sku)}</td><td>${esc(wh.warehouse_name)}${wh.warehouse_active ? "" : ' <span class="badge text-bg-secondary">Архив</span>'}</td><td class="text-end">${fmt(wh.on_hand)}</td><td class="text-end fw-semibold">${fmt(wh.available)}</td></tr>`), "Товары пока не добавлены");

    const filter = $("#inventory-search").value.trim().toLowerCase();
    putRows("#inventory-rows", allStock.filter(({item,wh}) => `${item.sku} ${item.name} ${wh.warehouse_name}`.toLowerCase().includes(filter)).map(({item,wh}) => `<tr><td class="sku">${esc(item.sku)}</td><td>${esc(item.name)}</td><td>${esc(wh.warehouse_name)}${wh.warehouse_active ? "" : ' <span class="badge text-bg-secondary">Архив</span>'}</td><td class="text-end">${fmt(wh.on_hand)}</td><td class="text-end">${fmt(wh.reserved)}</td><td class="text-end fw-semibold">${fmt(wh.available)}</td></tr>`));

    putRows("#warehouse-rows", state.warehouses.map((wh) => {
      const quantity = allStock.filter(({spot,wh:stockWh}) => stockWh.warehouse_id === wh.id).reduce((sum, row) => sum + row.wh.on_hand, 0);
      return `<tr><td class="sku">${esc(wh.code)}</td><td>${esc(wh.name)}</td><td>${wh.active ? '<span class="status-pill status-received">Активен</span>' : '<span class="status-pill status-archived">Архив</span>'}</td><td class="text-end">${fmt(quantity)}</td><td class="text-end table-action">${buttons("rename",wh.id,"Переименовать")}${wh.active ? buttons("archive",wh.id,"Архивировать","outline-danger") : ""}</td></tr>`;
    }));

    putRows("#transfer-rows", state.transfers.map((t) => `<tr><td class="sku">#${t.id}</td><td>${esc(productLabel(t.product_id))}</td><td>${esc(whLabel(t.source_warehouse_id))} → ${esc(whLabel(t.destination_warehouse_id))}</td><td class="text-end">${fmt(t.quantity)}</td><td>${statusPill(t.status)}</td><td class="text-end table-action">${t.status === "in_transit" ? buttons("receive_transfer",t.id,"Принять") + buttons("cancel_transfer",t.id,"Отменить","outline-danger") : "—"}</td></tr>`));

    putRows("#fbs-rows", state.fbs.map((r) => `<tr><td class="sku">#${r.id}</td><td>${esc(r.order_number || "—")}</td><td>${esc(productLabel(r.product_id))}</td><td>${esc(whLabel(r.warehouse_id))}</td><td class="text-end">${fmt(r.quantity)}</td><td>${statusPill(r.status)}</td><td class="text-end table-action">${r.status === "active" ? buttons("ship_fbs",r.id,"Отправить") + buttons("cancel_fbs",r.id,"Отменить","outline-secondary") : "—"}</td></tr>`));

    putRows("#fbo-rows", state.shipments.filter((s) => s.type === "FBO").map((s) => `<tr><td class="sku">#${s.id}</td><td>${esc(productLabel(s.product_id))}</td><td>${esc(whLabel(s.source_warehouse_id))}</td><td>${esc(s.destination || "—")}</td><td class="text-end">${fmt(s.quantity)}</td><td>${statusPill(s.status)}</td><td class="text-end table-action">${s.status === "in_transit" ? buttons("receive_fbo",s.id,"Подтвердить приёмку") : "—"}</td></tr>`));

    const movementRow = (m, condensed) => `<tr><td class="muted">${esc(asDate(m.created_at))}</td><td class="sku">${esc(m.sku)}</td><td>${esc(kinds[m.kind] || m.kind)}</td><td class="text-end">${fmt(m.quantity)}</td>${condensed ? `<td>${esc(m.source)} → ${esc(m.destination)}</td>` : `<td>${esc(m.source)}</td><td>${esc(m.destination)}</td>`}</tr>`;
    putRows("#overview-movements",state.movements.slice(0,8).map((m)=> movementRow(m,true)));
    putRows("#history-rows",state.movements.map((m)=>movementRow(m,false)));
  }

  async function loadData() {
    clearError();
    const [inventory, warehouses, transfers, fbs, shipments, movements] = await Promise.all([
      request("/api/inventory"),request("/api/warehouses"),request("/api/transfers"),request("/api/fbs"),request("/api/shipments"),request("/api/movements")
    ]);
    state.inventory = inventory; state.warehouses = warehouses.items; state.transfers = transfers.items;
    state.fbs = fbs.items; state.shipments = shipments.items; state.movements = movements.items;
    refreshUI();
  }

  const formSchemas = {
    receipt: {title:"Оформить приход",description:"Добавьте поступивший товар на выбранный склад.",url:"/api/inventory/receipt",fields:["product_id","warehouse_id","quantity"],success:"Приход зарегистрирован"},
    transfer: {title:"Перемещение между складами",description:"Товар уйдёт со склада отправления и появится в пути до приёмки.",url:"/api/transfers",fields:["product_id","source_warehouse_id","destination_warehouse_id","quantity"],success:"Перемещение создано"},
    reserve: {title:"Создать резерв FBS",description:"Резерв блокирует свободный остаток на выбранном складе.",url:"/api/fbs/reserve",fields:["product_id","warehouse_id","quantity","order_number"],success:"Резерв FBS создан"},
    fbo: {title:"Отправка на маркетплейс FBO",description:"Товар спишется с ФФ и будет числиться в пути до приёмки.",url:"/api/fbo/ship",fields:["product_id","warehouse_id","quantity","destination"],success:"Отправка FBO создана"},
    warehouse: {title:"Добавить склад",description:"Склад будет доступен для новых приходов и отправок.",url:"/api/warehouses",fields:["code","name"],success:"Склад добавлен"},
    product: {title:"Добавить товар",description:"Создайте новый артикул. Остатки можно добавить через приход.",url:"/api/products",fields:["sku","name"],success:"Товар создан"},
    rename: {title:"Переименовать склад",description:"Код склада и история останутся прежними.",url:null,fields:["name"],success:"Название склада изменено"}
  };
  const fieldNames = {product_id:"Товар", warehouse_id:"Склад", source_warehouse_id:"Склад отправления", destination_warehouse_id:"Склад назначения", quantity:"Количество, шт.", order_number:"Номер заказа (необязательно)", destination:"Название маркетплейса", code:"Код склада", name:"Название", sku:"Артикул"};

  function createField(field, id) {
    const fieldId = `f-${field}`;
    const label = `<label for="${fieldId}" class="form-label">${esc(fieldNames[field])}</label>`;
    if (field === "product_id") {
      return `<div class="mb-3">${label}<select class="form-select" name="${field}" id="${fieldId}" required>${state.inventory.items.map((p)=>`<option value="${p.id}">${esc(p.sku)} — ${esc(p.name)}</option>`).join("")}</select></div>`;
    }
    if (field.endsWith("warehouse_id")) {
      return `<div class="mb-3">${label}<select class="form-select" name="${field}" id="${fieldId}" required>${state.warehouses.filter((w)=>w.active).map((w)=>`<option value="${w.id}">${esc(w.name)}</option>`).join("")}</select></div>`;
    }
    if (field === "quantity") return `<div class="mb-3">${label}<input class="form-control" type="number" name="${field}" id="${fieldId}" min="1" max="1000000" value="1" required></div>`;
    const current = field === "name" && id ? state.warehouses.find((w) => w.id === id)?.name || "" : "";
    const max = {order_number:100,destination:180,code:50,name:220,sku:80}[field] || 100;
    return `<div class="mb-3">${label}<input class="form-control" type="text" name="${field}" id="${fieldId}" maxlength="${max}" value="${esc(current)}" ${field === "order_number" ? "" : "required"}></div>`;
  }

  function openOperation(operation, id = null) {
    const schema = formSchemas[operation];
    if (!schema) return;
    state.currentOperation = {operation,id};
    state.requestKey = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
    $("#operation-title").textContent = schema.title;
    $("#operation-description").textContent = schema.description;
    $("#operation-fields").innerHTML = schema.fields.map((field)=>createField(field,id)).join("");
    $("#form-error").classList.add("d-none");
    $("#operation-submit").disabled = false;
    modal.show();
  }

  async function submitOperation(event) {
    event.preventDefault();
    const {operation,id} = state.currentOperation || {};
    const schema = formSchemas[operation];
    if (!schema) return;
    const form = event.currentTarget;
    if (!form.reportValidity()) return;
    const data = Object.fromEntries(new FormData(form));
    for (const name of ["product_id","warehouse_id","source_warehouse_id","destination_warehouse_id","quantity"]) {
      if (name in data) data[name] = Number(data[name]);
    }
    if (data.source_warehouse_id && data.source_warehouse_id === data.destination_warehouse_id) {
      $("#form-error").textContent = "Выберите разные склады отправления и назначения.";
      $("#form-error").classList.remove("d-none");
      return;
    }
    if (data.order_number === "") data.order_number = null;
    if (["receipt","transfer","reserve","fbo"].includes(operation)) data.request_key = state.requestKey;
    const button = $("#operation-submit"); button.disabled = true;
    try {
      await request(operation === "rename" ? `/api/warehouses/${id}` : schema.url, {method:operation === "rename" ? "PATCH" : "POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(data)});
      modal.hide(); toast(schema.success); await loadData();
    } catch (error) {
      $("#form-error").textContent = error.message;
      $("#form-error").classList.remove("d-none");
    } finally { button.disabled = false; }
  }

  async function processAction(action,id) {
    const map = {
      receive_transfer: [`/api/transfers/${id}/receive`,"Приёмка перемещения подтверждена"],
      cancel_transfer: [`/api/transfers/${id}/cancel`,"Перемещение отменено. Товар возвращён на исходный ФФ"],
      cancel_fbs: [`/api/fbs/${id}/cancel`,"Резерв FBS отменён"],
      ship_fbs: [`/api/fbs/${id}/ship`,"FBS отправлен клиенту"],
      receive_fbo: [`/api/fbo/${id}/receive`,"FBO принят маркетплейсом"],
      archive: [`/api/warehouses/${id}`,"Склад архивирован"]
    };
    if (action === "rename") { openOperation("rename",id); return; }
    if (!map[action]) return;
    if (action === "archive" && !confirm("Архивировать склад? Остатки и история сохранятся, но новые операции на этом складе будут запрещены.")) return;
    if (action === "cancel_transfer" && !confirm("Отменить перемещение? Это вернёт весь товар в остаток склада отправления. Подтверждайте только если товар фактически не уехал или вернулся на исходный склад.")) return;
    try {
      await request(map[action][0],{method:action === "archive" ? "DELETE" : "POST"});
      toast(map[action][1]); await loadData();
    } catch (error) { showError(error.message); }
  }

  function setView(name) {
    if (!titles[name]) return;
    document.querySelectorAll(".app-view").forEach((view)=>view.classList.toggle("d-none",view.id !== `view-${name}`));
    document.querySelectorAll(".app-nav [data-view]").forEach((button)=>button.classList.toggle("active",button.dataset.view === name));
    $("#current-title").textContent = titles[name];
    $("#sidebar").classList.remove("open");
    history.replaceState(null,"",`#${name}`);
    window.scrollTo({top:0,behavior:"instant"});
  }

  document.addEventListener("click",async (event)=>{
    const control = event.target.closest("[data-operation], [data-process], [data-view], [data-go]");
    if (!control) return;
    if (control.dataset.operation) openOperation(control.dataset.operation);
    else if (control.dataset.process) {
      control.disabled = true;
      try { await processAction(control.dataset.process,Number(control.dataset.id)); }
      finally { control.disabled = false; }
    } else setView(control.dataset.view || control.dataset.go);
  });
  $("#menu-toggle").addEventListener("click",()=>$("#sidebar").classList.toggle("open"));
  $("#inventory-search").addEventListener("input",refreshUI);
  $("#refresh-btn").addEventListener("click",()=>loadData().then(()=>toast("Данные обновлены")).catch((e)=>showError(e.message)));
  $("#operation-form").addEventListener("submit",submitOperation);
  $("#operation-form").addEventListener("input",()=> { $("#form-error").classList.add("d-none"); });
  modal = bootstrap.Modal.getOrCreateInstance($("#operation-modal"));
  setView(location.hash.replace("#","") in titles ? location.hash.slice(1) : "overview");
  loadData().catch((error)=>showError(error.message));
})();
