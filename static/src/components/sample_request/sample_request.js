/** @odoo-module **/
// NUEVA SOLICITUD DE MUESTRAS — asistente guiado (27 sep 2026).
// Mismo patrón que "Solicitar entrega" (sale_delivery_wizard): pasos,
// validación en cliente y en servidor (sample_submit devuelve errores por
// campo), y al terminar la solicitud queda enviada a autorización.
import { Component, onWillStart, useRef, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const MODEL = "som.sample.request";

const ALL_STEPS = [
    { key: "client", short: "Cliente", title: "¿Para quién es la muestra?" },
    { key: "type", short: "Tipo", title: "¿Qué tipo de muestra?" },
    { key: "material", short: "Material", title: "Material a consumir" },
    { key: "workshop", short: "Taller", title: "¿Qué hace Taller?" },
    { key: "review", short: "Enviar", title: "Revisa y envía a autorización" },
];

const FIELD_STEP = {
    partner_id: "client",
    sample_type: "type",
    lines: "material",
    sizes: "workshop",
    reason: "review",
};

const MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];

export class SampleRequest extends Component {
    static template = "sale_stone_workshop_integration.SampleRequest";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.root = useRef("root");
        this.searchTimer = null;
        this.partnerTimer = null;
        this.state = useState({
            loading: true,
            loadError: "",
            data: null,
            step: 1,
            maxStep: 1,
            errors: {},
            submitting: false,
            done: null,
            // Paso 1
            partnerQuery: "",
            partnerResults: [],
            partner: null,
            projects: [],
            projectId: "",
            dateNeeded: "",
            address: "",
            // Paso 2
            sampleType: "",
            // Paso 3
            lotQuery: "",
            lotResults: [],
            searching: false,
            selected: [],
            // Paso 4: ruta de acabados en orden + corte final
            steps: [],
            processQuery: "",
            sizes: [],
            custom: { length: "", height: "", qty: 1 },
            // Paso 5
            reason: "",
            instructions: "",
        });
        onWillStart(async () => {
            try {
                this.state.data = await this.orm.call(MODEL, "sample_prepare", []);
            } catch (e) {
                this.state.loadError = (e && e.data && e.data.message) || "No se pudo abrir el asistente.";
            }
            this.state.loading = false;
            this.searchLots("");
        });
    }

    // ─── Pasos ───
    get steps() {
        const list = ALL_STEPS.filter((s) => s.key !== "workshop" || this.state.sampleType !== "delivery");
        return list.map((s, i) => ({ ...s, n: i + 1 }));
    }
    get currentStep() {
        return this.steps[this.state.step - 1] || this.steps[0];
    }
    get lastStep() {
        return this.steps.length;
    }
    stepClass(s) {
        return {
            on: s.n === this.state.step,
            done: s.n < this.state.step,
            reachable: s.n <= this.state.maxStep,
        };
    }
    clickStep(s) {
        if (s.n <= this.state.maxStep && s.n !== this.state.step) {
            if (s.n > this.state.step && !this.validateStep(this.currentStep.key)) {
                return;
            }
            this.goTo(s.n);
        }
    }
    goTo(n) {
        this.state.step = n;
        this.state.maxStep = Math.max(this.state.maxStep, n);
        const el = this.root.el;
        if (el) {
            el.scrollTop = 0;
        }
    }
    next() {
        if (!this.validateStep(this.currentStep.key)) {
            return;
        }
        this.goTo(Math.min(this.state.step + 1, this.lastStep));
    }
    back() {
        this.goTo(Math.max(this.state.step - 1, 1));
    }

    validateStep(key) {
        const e = {};
        if (key === "client" && !this.state.partner) {
            e.partner_id = "Elige el cliente.";
        }
        if (key === "type" && !this.state.sampleType) {
            e.sample_type = "Elige el tipo de muestra.";
        }
        if (key === "material") {
            if (!this.state.selected.length) {
                e.lines = "Agrega al menos un lote.";
            } else if (this.state.selected.some((l) => !(parseFloat(l.take) > 0) || parseFloat(l.take) > l.qty + 1e-6)) {
                e.lines = "Revisa las cantidades: deben ser mayores a cero y no pasar de lo disponible.";
            }
        }
        if (key === "workshop" && this.state.sampleType === "cut" && !this.state.sizes.length && !this.state.steps.length) {
            e.sizes = "Indica qué hace Taller: agrega acabados en orden y/o medidas de corte.";
        }
        if (key === "review" && !this.state.reason.trim()) {
            e.reason = "Escribe el motivo de la muestra.";
        }
        this.state.errors = e;
        return !Object.keys(e).length;
    }

    // ─── Paso 1: cliente ───
    onPartnerQuery(ev) {
        this.state.partnerQuery = ev.target.value;
        clearTimeout(this.partnerTimer);
        const q = this.state.partnerQuery.trim();
        if (q.length < 2) {
            this.state.partnerResults = [];
            return;
        }
        this.partnerTimer = setTimeout(async () => {
            const res = await this.orm.call("res.partner", "name_search", [], { name: q, limit: 12 });
            this.state.partnerResults = res.map((r) => ({ id: r[0], name: r[1] }));
        }, 250);
    }
    async pickPartner(p) {
        this.state.partner = p;
        this.state.partnerQuery = "";
        this.state.partnerResults = [];
        this.state.projectId = "";
        this.state.errors = {};
        const info = await this.orm.call(MODEL, "sample_partner_info", [p.id]);
        this.state.projects = info.projects || [];
        if (!this.state.address) {
            this.state.address = info.address || "";
        }
    }
    clearPartner() {
        this.state.partner = null;
        this.state.projects = [];
        this.state.projectId = "";
    }
    isProject(p) {
        return String(p.id) === String(this.state.projectId);
    }
    onProject(ev) {
        this.state.projectId = ev.target.value;
    }
    onDate(ev) {
        this.state.dateNeeded = ev.target.value;
    }
    onAddress(ev) {
        this.state.address = ev.target.value;
    }

    // ─── Paso 2: tipo ───
    setType(t) {
        this.state.sampleType = t;
        this.state.errors = {};
        // Cambiar de tipo reacomoda los pasos: no se puede saltar a pasos no vistos.
        this.state.maxStep = Math.min(this.state.maxStep, this.state.step);
    }

    // ─── Paso 3: material ───
    onLotQuery(ev) {
        this.state.lotQuery = ev.target.value;
        clearTimeout(this.searchTimer);
        this.searchTimer = setTimeout(() => this.searchLots(this.state.lotQuery), 300);
    }
    async searchLots(q) {
        this.state.searching = true;
        try {
            const exclude = this.state.selected.map((l) => l.lot_id);
            this.state.lotResults = await this.orm.call(MODEL, "sample_search_lots", [q || "", exclude, 40]);
        } finally {
            this.state.searching = false;
        }
    }
    addLot(lot) {
        if (this.state.selected.some((l) => l.lot_id === lot.lot_id)) {
            return;
        }
        this.state.selected.push({ ...lot, take: lot.qty });
        this.state.lotResults = this.state.lotResults.filter((l) => l.lot_id !== lot.lot_id);
        this.state.errors = {};
    }
    removeLot(lot) {
        this.state.selected = this.state.selected.filter((l) => l.lot_id !== lot.lot_id);
        this.state.sizes = this.state.sizes.filter((s) => !s.product_id || this.selectedProducts.some((p) => p.id === s.product_id));
        this.state.steps = this.state.steps.filter((s) => !s.product_id || this.selectedProducts.some((p) => p.id === s.product_id));
        this.searchLots(this.state.lotQuery);
    }
    onTake(lot, ev) {
        lot.take = ev.target.value;
    }
    get selectedProducts() {
        const seen = {};
        const out = [];
        for (const l of this.state.selected) {
            if (!seen[l.product_id]) {
                seen[l.product_id] = true;
                out.push({ id: l.product_id, name: l.product });
            }
        }
        return out;
    }
    get totalM2() {
        let t = 0;
        for (const l of this.state.selected) {
            const take = parseFloat(l.take) || 0;
            if (l.is_m2) {
                t += take;
            } else if (l.width && l.height) {
                t += (l.width * l.height) / 10000;
            }
        }
        return t;
    }

    // ─── Paso 4: ruta de acabados (en orden) ───
    get processes() {
        const q = (this.state.processQuery || "").trim().toLowerCase();
        const all = (this.state.data && this.state.data.processes) || [];
        return q ? all.filter((p) => p.name.toLowerCase().includes(q)) : all;
    }
    onProcessQuery(ev) {
        this.state.processQuery = ev.target.value;
    }
    addStep(p) {
        this.state.steps.push({ key: Date.now() + Math.random(), process_id: p.id, name: p.name, product_id: 0, note: "" });
        this.state.errors = {};
    }
    moveStep(s, delta) {
        const list = this.state.steps;
        const i = list.findIndex((x) => x.key === s.key);
        const j = i + delta;
        if (i < 0 || j < 0 || j >= list.length) {
            return;
        }
        const [item] = list.splice(i, 1);
        list.splice(j, 0, item);
    }
    removeStep(s) {
        this.state.steps = this.state.steps.filter((x) => x.key !== s.key);
    }
    onStepProduct(s, ev) {
        s.product_id = parseInt(ev.target.value, 10) || 0;
    }
    onStepNote(s, ev) {
        s.note = ev.target.value;
    }
    /** "1) MATIZADO MARMOL → 2) BUSARDEADO MARMOL → 3) Corte: …" */
    get routeText() {
        const parts = this.state.steps.map((s) => s.name + (s.product_id ? ` (${this.productName(s.product_id)})` : ""));
        if (this.state.sizes.length) {
            parts.push("Corte: " + this.state.sizes.map((s) => `${this.fmtNum(s.length)}×${this.fmtNum(s.height)} cm × ${s.qty}`).join(", "));
        }
        return parts.map((p, i) => `${i + 1}) ${p}`).join("  →  ");
    }

    // ─── Paso 4: corte final ───
    addPreset(p) {
        const found = this.state.sizes.find((s) => s.length === p.l && s.height === p.h && !s.product_id);
        if (found) {
            found.qty += 1;
        } else {
            this.state.sizes.push({ key: Date.now() + Math.random(), length: p.l, height: p.h, qty: 1, product_id: 0, note: "" });
        }
        this.state.errors = {};
    }
    onCustom(field, ev) {
        this.state.custom[field] = ev.target.value;
    }
    addCustom() {
        const l = parseFloat(this.state.custom.length);
        const h = parseFloat(this.state.custom.height);
        const q = parseInt(this.state.custom.qty, 10);
        if (!(l > 0) || !(h > 0) || !(q > 0)) {
            this.state.errors = { sizes: "La medida necesita largo, alto y piezas mayores a cero." };
            return;
        }
        this.state.sizes.push({ key: Date.now() + Math.random(), length: l, height: h, qty: q, product_id: 0, note: "" });
        this.state.custom = { length: "", height: "", qty: 1 };
        this.state.errors = {};
    }
    sizeQty(s, delta) {
        s.qty = Math.max(1, (parseInt(s.qty, 10) || 1) + delta);
    }
    onSizeProduct(s, ev) {
        s.product_id = parseInt(ev.target.value, 10) || 0;
    }
    onSizeNote(s, ev) {
        s.note = ev.target.value;
    }
    removeSize(s) {
        this.state.sizes = this.state.sizes.filter((x) => x.key !== s.key);
    }
    get totalPieces() {
        return this.state.sizes.reduce((a, s) => a + (parseInt(s.qty, 10) || 0), 0);
    }
    productName(id) {
        const p = this.selectedProducts.find((x) => x.id === id);
        return p ? p.name : "Todos los materiales";
    }

    // ─── Paso 5 y envío ───
    onReason(ev) {
        this.state.reason = ev.target.value;
    }
    onInstructions(ev) {
        this.state.instructions = ev.target.value;
    }
    get projectName() {
        const p = this.state.projects.find((x) => String(x.id) === String(this.state.projectId));
        return p ? p.name : "";
    }
    async submit() {
        for (const s of this.steps) {
            if (!this.validateStep(s.key)) {
                this.goTo(s.n);
                return;
            }
        }
        this.state.submitting = true;
        try {
            const res = await this.orm.call(MODEL, "sample_submit", [{
                partner_id: this.state.partner.id,
                project_id: parseInt(this.state.projectId, 10) || false,
                sample_type: this.state.sampleType,
                date_needed: this.state.dateNeeded || false,
                delivery_address: this.state.address,
                reason: this.state.reason,
                instructions: this.state.instructions,
                lines: this.state.selected.map((l) => ({ lot_id: l.lot_id, qty: parseFloat(l.take) || 0 })),
                steps: this.state.steps.map((s) => ({ process_id: s.process_id, product_id: s.product_id || false, note: s.note })),
                sizes: this.state.sizes.map((s) => ({
                    length: s.length, height: s.height, qty: s.qty, product_id: s.product_id || false, note: s.note,
                })),
            }]);
            if (res.errors) {
                this.state.errors = res.errors;
                const key = Object.keys(res.errors).find((k) => FIELD_STEP[k]);
                if (key) {
                    const st = this.steps.find((s) => s.key === FIELD_STEP[key]);
                    if (st) {
                        this.goTo(st.n);
                    }
                }
                if (res.errors.general) {
                    this.notification.add(res.errors.general, { type: "danger", sticky: true });
                }
                return;
            }
            this.state.done = res;
        } finally {
            this.state.submitting = false;
        }
    }
    openRequest() {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: MODEL,
            res_id: this.state.done.id,
            views: [[false, "form"]],
            target: "current",
        });
    }
    openList() {
        this.action.doAction("sale_stone_workshop_integration.action_som_sample_request_open");
    }
    another() {
        this.action.doAction("sale_stone_workshop_integration.action_som_sample_new", { clearBreadcrumbs: true });
    }

    // ─── Formato ───
    fmtQty(v) {
        return (parseFloat(v) || 0).toLocaleString("es-MX", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }
    fmtDims(l) {
        return l.width && l.height ? `${this.fmtNum(l.width)} × ${this.fmtNum(l.height)} cm` : "";
    }
    fmtNum(v) {
        return (parseFloat(v) || 0).toLocaleString("es-MX", { maximumFractionDigits: 1 });
    }
    fmtIso(iso) {
        if (!iso) {
            return "Sin fecha";
        }
        const [y, m, d] = iso.split("-").map((x) => parseInt(x, 10));
        return `${d} ${MESES[m - 1]} ${y}`;
    }
}

registry.category("actions").add("som_sample_request", SampleRequest);
