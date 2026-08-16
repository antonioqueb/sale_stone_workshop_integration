/** @odoo-module **/
// Columna "A taller" del Tablero de Salidas.
//
// El vendedor crea la orden de taller desde su venta y ahí termina su
// parte. El material sigue en el almacén, reservado en un traslado interno
// que nadie validaba porque nadie se enteraba. Aquí Logística lo ve junto
// a los pick tickets y las remisiones, y lo entrega de un clic.
//
// Se hace por patch y no copiando el tablero: el de Salidas es de
// sale_delivery_wizard y debe seguir siendo el dueño. Aquí solo se le
// cuelga el comportamiento que el taller necesita.
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { OutboundDashboard } from "@sale_delivery_wizard/components/outbound_dashboard/outbound_dashboard";

patch(OutboundDashboard.prototype, {
    setup() {
        super.setup();
        // El tablero base no usa notificaciones; aquí sí hacen falta para
        // avisar cuando un traslado no se puede entregar.
        this.notification = useService("notification");
    },

    /** Entrega el material al taller validando el traslado reservado. */
    async deliverToWorkshop(pickingId) {
        if (this.somDelivering) {
            return; // doble clic: el traslado ya va en camino
        }
        this.somDelivering = true;
        try {
            const res = await this.orm.call(
                "sale.delivery.live.map",
                "som_deliver_to_workshop",
                [pickingId]
            );
            if (res && res.action) {
                // Odoo pide confirmar algo (backorder, cantidades): se abre
                // su ventana en vez de adivinar por él.
                await this.action.doAction(res.action, {
                    onClose: () => this.load(),
                });
                return;
            }
            if (res && res.error) {
                this.notification.add(res.error, { type: "danger" });
            }
            await this.load();
        } finally {
            this.somDelivering = false;
        }
    },

    /** Abre el traslado por si hay que revisarlo antes de entregar. */
    openWorkshopPicking(pickingId) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "stock.picking",
            res_id: pickingId,
            views: [[false, "form"]],
            target: "current",
        });
    },
});
