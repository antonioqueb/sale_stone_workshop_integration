/** @odoo-module **/

import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { Component, useState, onWillStart, onWillUpdateProps, onWillUnmount } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

export class SaleWorkshopInputSelector extends Component {
    static template = "sale_stone_workshop_integration.SaleWorkshopInputSelector";
    static props = {
        ...standardFieldProps,
    };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");

        this._popupRoot = null;
        this._keyHandler = null;
        this._searchTimeout = null;

        this.state = useState({
            isLoading: true,
            canSelect: false,
            tooltip: "Cargando...",
            selectedCount: 0,
            selectedQty: 0,
            data: null,
        });

        onWillStart(async () => {
            await this.loadLineData();
        });

        onWillUpdateProps(async () => {
            await this.loadLineData();
        });

        onWillUnmount(() => {
            this.destroyPopup();
        });
    }

    _getRecordId() {
        const record = this.props.record;

        if (record?.resId) {
            const id = parseInt(record.resId, 10);
            return Number.isInteger(id) && id > 0 ? id : null;
        }

        if (record?.data?.id) {
            const id = parseInt(record.data.id, 10);
            return Number.isInteger(id) && id > 0 ? id : null;
        }

        return null;
    }

    fmt(num) {
        if (num === null || num === undefined || isNaN(num)) {
            return "0.00";
        }
        return parseFloat(num).toFixed(2);
    }

    _escapeHtml(text) {
        if (text === null || text === undefined) {
            return "";
        }
        return String(text)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    async loadLineData() {
        const lineId = this._getRecordId();

        if (!lineId) {
            this.state.isLoading = false;
            this.state.canSelect = false;
            this.state.tooltip = "Guarda la línea antes de seleccionar placas base.";
            this.state.selectedCount = 0;
            this.state.selectedQty = 0;
            this.state.data = null;
            return;
        }

        this.state.isLoading = true;

        try {
            const data = await this.orm.call(
                "sale.order.line",
                "get_workshop_input_selector_data",
                [[lineId]]
            );

            this.state.data = data || {};
            this.state.canSelect = !!data?.can_select;
            this.state.selectedCount = data?.selected_count || 0;
            this.state.selectedQty = data?.selected_qty || 0;

            if (!data?.workshop_required) {
                this.state.tooltip = "Esta línea no requiere taller.";
            } else if (!data?.base_product_id) {
                this.state.tooltip = "Configura el producto base.";
            } else if (!data?.process_id) {
                this.state.tooltip = "Configura el proceso de taller.";
            } else if (!data?.can_select) {
                this.state.tooltip = "Selección no disponible (orden cancelada o sin configurar).";
            } else if (data?.state === "draft" || data?.state === "sent") {
                this.state.tooltip = `Pre-seleccionar placas base (no se apartan hasta confirmar): ${data.base_product_name || ""}`;
            } else {
                this.state.tooltip = `Seleccionar placas base: ${data.base_product_name || ""}`;
            }
        } catch (error) {
            console.warn("[SWIS] Error cargando datos:", error);
            this.state.canSelect = false;
            this.state.tooltip = error.message || "No se pudo cargar la selección.";
        } finally {
            this.state.isLoading = false;
        }
    }

    _warn(message) {
        this.notification.add(message, { type: "warning" });
    }

    async openPopup() {
        await this.loadLineData();

        const data = this.state.data;

        if (!data || !data.workshop_required) {
            this._warn("Esta línea no requiere taller.");
            return;
        }

        if (!data.base_product_id) {
            this._warn("Configura el producto base antes de seleccionar placas.");
            return;
        }

        if (!data.process_id) {
            this._warn("Configura el proceso de taller antes de seleccionar placas.");
            return;
        }

        if (!data.can_select) {
            this._warn("La selección de placas base solo está disponible en órdenes de venta confirmadas.");
            return;
        }

        this.destroyPopup();

        const root = document.createElement("div");
        root.className = "stone-popup-root swis-popup-root";
        document.body.appendChild(root);
        this._popupRoot = root;

        await this._renderPopup(data);
    }

    destroyPopup() {
        if (this._searchTimeout) {
            clearTimeout(this._searchTimeout);
            this._searchTimeout = null;
        }

        if (this._keyHandler) {
            document.removeEventListener("keydown", this._keyHandler);
            this._keyHandler = null;
        }

        if (this._popupRoot) {
            this._popupRoot.remove();
            this._popupRoot = null;
        }
    }

    async _renderPopup(data) {
        const root = this._popupRoot;
        const PAGE_SIZE = 35;

        const st = {
            items: [],
            total: 0,
            page: 0,
            isLoading: false,
            pendingIds: new Set(data.selected_lot_ids || []),
            pendingBreakdown: { ...(data.breakdown || {}) },
            filters: {
                product_name: "",
                lot_name: "",
                bloque: "",
                atado: "",
                alto_min: "",
                ancho_min: "",
                tipo: "",
            },
        };

        // MISMO lenguaje visual que el selector de placas de la venta
        // (clases stone-* de sale_stone_selection): header con badges,
        // tarjetas Solicitado/Seleccionado/Pendiente con barra de avance,
        // filtros y tabla idénticos.
        const requestedQty = parseFloat(data.requested_qty || 0);
        root.innerHTML = `
            <div class="stone-popup-overlay" id="swis-overlay">
                <div class="stone-popup-container">
                    <div class="stone-popup-header">
                        <div class="stone-popup-title">
                            <i class="fa fa-cubes"></i>
                            Placas base a consumir en taller
                            <span class="stone-popup-subtitle">— ${this._escapeHtml(data.base_product_name || "")} → ${this._escapeHtml(data.product_final_name || "")}</span>
                        </div>

                        <div class="stone-popup-header-actions">
                            <span class="stone-badge-requested">
                                <i class="fa fa-bullseye me-1"></i>
                                Solicitado <span>${this.fmt(requestedQty)}</span>
                            </span>

                            <span class="stone-badge-selected">
                                <i class="fa fa-check-circle me-1"></i>
                                <span id="swis-count">0</span> selec.
                            </span>

                            <span class="stone-badge-qty-total">
                                <i class="fa fa-balance-scale me-1"></i>
                                <span id="swis-total">0.00</span>
                            </span>

                            <button class="stone-btn stone-btn-primary-dark" id="swis-confirm-top">
                                <i class="fa fa-check me-1"></i> Confirmar
                            </button>

                            <button class="stone-btn stone-btn-ghost" id="swis-close">
                                <i class="fa fa-times"></i>
                            </button>
                        </div>
                    </div>

                    <div class="stone-popup-allocation-summary">
                        <div class="stone-allocation-card stone-allocation-target">
                            <span class="stone-allocation-label">Solicitado</span>
                            <strong>${this.fmt(requestedQty)}</strong>
                        </div>
                        <div class="stone-allocation-card stone-allocation-selected">
                            <span class="stone-allocation-label">Seleccionado</span>
                            <strong id="swis-sum-selected">0.00</strong>
                        </div>
                        <div class="stone-allocation-card stone-allocation-remaining">
                            <span class="stone-allocation-label">Pendiente</span>
                            <strong id="swis-sum-pending">${this.fmt(requestedQty)}</strong>
                        </div>
                        <div class="stone-allocation-progress-box">
                            <div class="stone-allocation-progress-head">
                                <span id="swis-progress-text">0.00 de ${this.fmt(requestedQty)}</span>
                                <strong id="swis-progress-label">0%</strong>
                            </div>
                            <div class="stone-allocation-progress-track">
                                <div class="stone-allocation-progress-fill" id="swis-progress-fill"></div>
                            </div>
                        </div>
                    </div>

                    <div class="stone-popup-filters">
                        <div class="stone-filter-group">
                            <label>Material</label>
                            <input class="stone-filter-input" id="swis-f-product" type="text" placeholder="Cualquier material (vacío = producto base)"/>
                        </div>

                        <div class="stone-filter-group">
                            <label>Lote</label>
                            <input class="stone-filter-input" id="swis-f-lot" type="text" placeholder="Buscar lote..."/>
                        </div>

                        <div class="stone-filter-group">
                            <label>Bloque</label>
                            <input class="stone-filter-input" id="swis-f-bloque" type="text" placeholder="Bloque..."/>
                        </div>

                        <div class="stone-filter-group">
                            <label>Atado</label>
                            <input class="stone-filter-input" id="swis-f-atado" type="text" placeholder="Atado..."/>
                        </div>

                        <div class="stone-filter-group">
                            <label>Alto mín.</label>
                            <input class="stone-filter-input stone-filter-sm" id="swis-f-alto" type="number" step="0.01"/>
                        </div>

                        <div class="stone-filter-group">
                            <label>Largo mín.</label>
                            <input class="stone-filter-input stone-filter-sm" id="swis-f-ancho" type="number" step="0.01"/>
                        </div>

                        <div class="stone-filter-group">
                            <label>Tipo</label>
                            <select class="stone-filter-input stone-filter-sm" id="swis-f-tipo">
                                <option value="">Todos</option>
                                <option value="placa">Placa</option>
                                <option value="formato">Formato</option>
                                <option value="pieza">Pieza</option>
                                <option value="retazo">Retazo</option>
                            </select>
                        </div>

                        <div class="stone-filter-actions">
                            <button class="stone-btn stone-btn-clear-all" id="swis-clear">
                                <i class="fa fa-square-o me-1"></i> Limpiar
                            </button>
                        </div>

                        <div class="stone-filter-stats" id="swis-stat">Buscando...</div>
                    </div>

                    <div class="stone-popup-body" id="swis-body">
                        <div class="stone-empty-state">
                            <i class="fa fa-circle-o-notch fa-spin fa-2x"></i>
                            <div class="stone-empty-text mt-2">Cargando inventario...</div>
                        </div>
                    </div>

                    <div class="stone-popup-footer">
                        <span class="stone-footer-info" id="swis-footer-info">—</span>
                        <div class="stone-footer-adjust">
                            <button class="stone-btn stone-btn-outline" id="swis-prev">
                                <i class="fa fa-chevron-left me-1"></i> Anterior
                            </button>
                            <span id="swis-page-info">—</span>
                            <button class="stone-btn stone-btn-outline" id="swis-next">
                                Siguiente <i class="fa fa-chevron-right ms-1"></i>
                            </button>
                        </div>
                        <div class="stone-footer-actions">
                            <button class="stone-btn stone-btn-outline" id="swis-cancel">Cancelar</button>
                            <button class="stone-btn stone-btn-primary-dark" id="swis-confirm-bottom">
                                <i class="fa fa-check me-1"></i> Agregar selección
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        `;

        const close = () => this.destroyPopup();

        root.querySelector("#swis-close").addEventListener("click", close);
        root.querySelector("#swis-cancel").addEventListener("click", close);
        root.querySelector("#swis-overlay").addEventListener("click", (ev) => {
            if (ev.target.id === "swis-overlay") {
                close();
            }
        });

        this._keyHandler = (ev) => {
            if (ev.key === "Escape") {
                close();
            }
        };
        document.addEventListener("keydown", this._keyHandler);

        const updateSummary = () => {
            const count = st.pendingIds.size;
            let total = 0;

            for (const lotId of st.pendingIds) {
                const qty = parseFloat(st.pendingBreakdown[String(lotId)] || 0);
                total += qty || 0;
            }

            root.querySelector("#swis-count").textContent = String(count);
            root.querySelector("#swis-total").textContent = this.fmt(total);

            // Tarjetas + barra de avance (mismo patrón que el selector de
            // placas de la venta).
            const pending = Math.max(requestedQty - total, 0);
            const pct = requestedQty > 0 ? Math.min(100, (total / requestedQty) * 100) : 0;
            const sumSel = root.querySelector("#swis-sum-selected");
            const sumPen = root.querySelector("#swis-sum-pending");
            const pFill = root.querySelector("#swis-progress-fill");
            const pText = root.querySelector("#swis-progress-text");
            const pLabel = root.querySelector("#swis-progress-label");
            if (sumSel) sumSel.textContent = this.fmt(total);
            if (sumPen) sumPen.textContent = this.fmt(pending);
            if (pFill) pFill.style.width = pct.toFixed(1) + "%";
            if (pText) pText.textContent = `${this.fmt(total)} de ${this.fmt(requestedQty)}`;
            if (pLabel) pLabel.textContent = Math.round(pct) + "%";
        };

        const updatePager = () => {
            const prevBtn = root.querySelector("#swis-prev");
            const nextBtn = root.querySelector("#swis-next");
            const pageInfo = root.querySelector("#swis-page-info");

            if (!prevBtn || !nextBtn || !pageInfo) {
                return;
            }

            const totalPages = Math.max(1, Math.ceil(st.total / PAGE_SIZE));

            prevBtn.disabled = st.isLoading || st.page <= 0;
            nextBtn.disabled = st.isLoading || st.page >= totalPages - 1;
            pageInfo.textContent = `Página ${st.page + 1} de ${totalPages}`;
        };

        const render = () => {
            const body = root.querySelector("#swis-body");
            const stat = root.querySelector("#swis-stat");
            const footer = root.querySelector("#swis-footer-info");

            updatePager();

            if (st.isLoading && !st.items.length) {
                body.innerHTML = `
                    <div class="stone-empty-state">
                        <i class="fa fa-circle-o-notch fa-spin fa-2x"></i>
                        <div class="stone-empty-text mt-2">Cargando inventario...</div>
                    </div>`;
                stat.innerHTML = '<i class="fa fa-circle-o-notch fa-spin me-1"></i> Buscando...';
                updateSummary();
                return;
            }

            if (!st.items.length) {
                body.innerHTML = `
                    <div class="stone-empty-state">
                        <i class="fa fa-inbox fa-2x"></i>
                        <div class="stone-empty-text mt-2">Sin resultados. Usa el filtro Material para buscar lotes de otros productos/acabados.</div>
                    </div>`;
                stat.textContent = "0 lotes";
                footer.textContent = "Sin resultados disponibles.";
                updateSummary();
                return;
            }

            let rows = "";

            for (const q of st.items) {
                const lotId = q.lot_id ? q.lot_id[0] : 0;
                const lotName = q.lot_id ? q.lot_id[1] : "";

                if (!lotId) {
                    continue;
                }

                const selected = st.pendingIds.has(lotId);
                const tipo = String(q.x_tipo || "placa").toLowerCase();
                const isPartial = tipo === "formato" || tipo === "pieza" || tipo === "retazo";
                const qtyAvailable = parseFloat(q.quantity || 0);
                const qtyValue = parseFloat(st.pendingBreakdown[String(lotId)] || qtyAvailable || 0);
                const loc = q.location_id ? q.location_id[1] : "";
                const productName = q.product_id ? q.product_id[1] : "";
                const isBase = q.is_base_product !== false;

                rows += `
                    <tr class="${selected ? "row-sel" : ""}" data-lot-id="${lotId}">
                        <td class="col-chk">
                            <div class="stone-chkbox ${selected ? "checked" : ""}">
                                ${selected ? '<i class="fa fa-check"></i>' : ""}
                            </div>
                        </td>
                        <td class="cell-lot">${this._escapeHtml(lotName)}</td>
                        <td class="${isBase ? "" : "swis-product-alt"}"
                            title="${this._escapeHtml(productName)}">
                            ${this._escapeHtml(productName || "-")}
                        </td>
                        <td>${this._escapeHtml(q.x_bloque || "-")}</td>
                        <td>${this._escapeHtml(q.x_atado || "-")}</td>
                        <td class="col-num">${q.x_alto ? this.fmt(q.x_alto) : "-"}</td>
                        <td class="col-num">${q.x_ancho ? this.fmt(q.x_ancho) : "-"}</td>
                        <td class="col-num fw-semibold swis-qty-available">${this.fmt(qtyAvailable)}</td>
                        <td><span class="stone-tag stone-tag-tipo-${this._escapeHtml(tipo)}">${this._escapeHtml(tipo || "placa")}</span></td>
                        <td>${this._escapeHtml(q.x_color || "-")}</td>
                        <td class="cell-loc">${this._escapeHtml(loc)}</td>
                        <td class="col-num col-popup-qty">
                            ${
                                isPartial
                                    ? `<input class="stone-popup-qty-input swis-qty-input" type="number" step="0.01" min="0" max="${qtyAvailable}" data-lot-id="${lotId}" value="${qtyValue || 0}"/>`
                                    : `<span class="fw-semibold">${this.fmt(qtyAvailable)}</span>`
                            }
                        </td>
                    </tr>
                `;
            }

            body.innerHTML = `
                <table class="stone-popup-table">
                    <thead>
                        <tr>
                            <th class="col-chk">✓</th>
                            <th>Lote</th>
                            <th>Material</th>
                            <th>Bloque</th>
                            <th>Atado</th>
                            <th class="col-num">Alto</th>
                            <th class="col-num">Largo</th>
                            <th class="col-num">Disp.</th>
                            <th>Tipo</th>
                            <th>Color</th>
                            <th>Ubicación</th>
                            <th class="col-num">A consumir</th>
                        </tr>
                    </thead>
                    <tbody>${rows}</tbody>
                </table>
            `;

            stat.textContent = `${st.total} lote(s)`;
            const from = st.total ? st.page * PAGE_SIZE + 1 : 0;
            const to = st.page * PAGE_SIZE + st.items.length;
            footer.textContent = `Mostrando ${from}–${to} de ${st.total} lote(s).`;

            body.querySelectorAll("tr[data-lot-id]").forEach((tr) => {
                tr.addEventListener("click", (ev) => {
                    if (ev.target.classList.contains("swis-qty-input")) {
                        return;
                    }

                    const lotId = parseInt(tr.dataset.lotId, 10);
                    const qtyText = tr.querySelector(".swis-qty-available")?.textContent || "0";
                    const qty = parseFloat(qtyText) || 0;

                    if (st.pendingIds.has(lotId)) {
                        st.pendingIds.delete(lotId);
                        delete st.pendingBreakdown[String(lotId)];
                    } else {
                        st.pendingIds.add(lotId);
                        st.pendingBreakdown[String(lotId)] = qty;
                    }

                    render();
                    updateSummary();
                });
            });

            body.querySelectorAll(".swis-qty-input").forEach((input) => {
                input.addEventListener("click", (ev) => ev.stopPropagation());
                input.addEventListener("input", (ev) => {
                    const lotId = parseInt(ev.target.dataset.lotId, 10);
                    const max = parseFloat(ev.target.max || 0);
                    let val = parseFloat(ev.target.value || 0);

                    if (val < 0) {
                        val = 0;
                    }

                    if (max && val > max) {
                        val = max;
                        ev.target.value = val;
                    }

                    if (val > 0) {
                        st.pendingIds.add(lotId);
                        st.pendingBreakdown[String(lotId)] = val;
                    } else {
                        st.pendingIds.delete(lotId);
                        delete st.pendingBreakdown[String(lotId)];
                    }

                    updateSummary();
                });
            });

            updateSummary();
        };

        const load = async (page = 0) => {
            st.isLoading = true;
            render();

            try {
                const lineId = this._getRecordId();

                if (!lineId) {
                    throw new Error("Guarda la línea antes de buscar placas base.");
                }

                const result = await this.orm.call(
                    "sale.order.line",
                    "search_workshop_input_inventory_for_selector",
                    [[lineId]],
                    {
                        filters: st.filters,
                        current_lot_ids: Array.from(st.pendingIds),
                        page,
                        page_size: PAGE_SIZE,
                    }
                );

                st.items = result?.items || [];
                st.total = result?.total || 0;
                st.page = page;
            } catch (error) {
                console.error("[SWIS] Error buscando inventario:", error);
                this.notification.add(
                    error.message || "No se pudo cargar el inventario base.",
                    { type: "danger" }
                );
                st.items = [];
                st.total = 0;
            } finally {
                st.isLoading = false;
                render();
            }
        };

        const confirm = async () => {
            const lineId = this._getRecordId();

            if (!lineId) {
                this._warn("Guarda la línea antes de seleccionar placas base.");
                return;
            }

            try {
                const result = await this.orm.call(
                    "sale.order.line",
                    "write_workshop_input_selection_from_lots",
                    [[lineId]],
                    {
                        lot_ids: Array.from(st.pendingIds),
                        breakdown: st.pendingBreakdown,
                    }
                );

                this.state.data = result;
                this.state.selectedCount = result?.selected_count || 0;
                this.state.selectedQty = result?.selected_qty || 0;

                this.notification.add("Placas base actualizadas.", { type: "success" });

                this.destroyPopup();
                await this.loadLineData();
            } catch (error) {
                console.error("[SWIS] Error confirmando selección:", error);
                this.notification.add(
                    error.message || "No se pudo guardar la selección.",
                    { type: "danger" }
                );
            }
        };

        root.querySelector("#swis-confirm-top").addEventListener("click", confirm);
        root.querySelector("#swis-confirm-bottom").addEventListener("click", confirm);

        root.querySelector("#swis-prev").addEventListener("click", () => {
            if (!st.isLoading && st.page > 0) {
                load(st.page - 1);
            }
        });

        root.querySelector("#swis-next").addEventListener("click", () => {
            const totalPages = Math.max(1, Math.ceil(st.total / PAGE_SIZE));
            if (!st.isLoading && st.page < totalPages - 1) {
                load(st.page + 1);
            }
        });

        root.querySelector("#swis-clear").addEventListener("click", () => {
            st.pendingIds = new Set();
            st.pendingBreakdown = {};
            render();
            updateSummary();
        });

        const bindFilter = (id, key) => {
            const el = root.querySelector(id);

            if (!el) {
                return;
            }

            el.addEventListener("input", () => {
                st.filters[key] = el.value || "";

                if (this._searchTimeout) {
                    clearTimeout(this._searchTimeout);
                }

                this._searchTimeout = setTimeout(() => load(0), 350);
            });

            el.addEventListener("change", () => {
                st.filters[key] = el.value || "";
                load(0);
            });
        };

        bindFilter("#swis-f-product", "product_name");
        bindFilter("#swis-f-lot", "lot_name");
        bindFilter("#swis-f-bloque", "bloque");
        bindFilter("#swis-f-atado", "atado");
        bindFilter("#swis-f-alto", "alto_min");
        bindFilter("#swis-f-ancho", "ancho_min");
        bindFilter("#swis-f-tipo", "tipo");

        await load(0);
    }
}

export const saleWorkshopInputSelectorField = {
    component: SaleWorkshopInputSelector,
    supportedTypes: ["boolean"],
};

registry.category("fields").add("sale_workshop_input_selector", saleWorkshopInputSelectorField);