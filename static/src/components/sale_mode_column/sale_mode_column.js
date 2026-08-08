/** @odoo-module **/
import { registry } from "@web/core/registry";
import { Component } from "@odoo/owl";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

/**
 * Columna única "Modos" en las líneas de la orden de venta: apila en
 * vertical los tres interruptores que antes ocupaban tres columnas
 * (Pedir = auto_transit_assign, Asignar = por_asignar,
 * Taller = stone_workshop_required). Se ancla al campo
 * auto_transit_assign; los otros dos llegan como fieldDependencies.
 */
const MODES = [
    { field: "auto_transit_assign", label: "Pedir" },
    { field: "por_asignar", label: "Asignar" },
    { field: "stone_workshop_required", label: "Taller" },
];

export class SomSaleModeColumn extends Component {
    static template = "sale_stone_workshop_integration.SomSaleModeColumn";
    static props = { ...standardFieldProps };

    get modes() {
        return MODES;
    }

    // Reglas por tipo de línea:
    // - SERVICIOS: ningún modo (la celda queda vacía).
    // - Pedir/Asignar: todo lo demás (placas, formatos, piezas, adhesivos).
    // - Taller: SOLO placas y formatos (stone_workshop_kind_allowed).
    get visibleModes() {
        const data = this.props.record.data;
        if (data.tc_is_service_line) {
            return [];
        }
        return MODES.filter((m) => {
            if (m.field === "stone_workshop_required") {
                return Boolean(data.stone_workshop_kind_allowed);
            }
            return true;
        });
    }

    isOn(fname) {
        return !!this.props.record.data[fname];
    }

    isDisabled(fname) {
        return !!this.props.readonly;
    }

    async toggle(fname, ev) {
        if (ev) {
            ev.preventDefault();
            ev.stopPropagation();
        }
        if (this.isDisabled(fname)) {
            return;
        }
        const rec = this.props.record;
        await rec.update({ [fname]: !rec.data[fname] }, { save: true });
    }
}

registry.category("fields").add("som_sale_mode_column", {
    component: SomSaleModeColumn,
    supportedTypes: ["boolean"],
    fieldDependencies: [
        { name: "por_asignar", type: "boolean" },
        { name: "stone_workshop_required", type: "boolean" },
        { name: "tc_is_service_line", type: "boolean" },
        { name: "stone_workshop_kind_allowed", type: "boolean" },
    ],
});
