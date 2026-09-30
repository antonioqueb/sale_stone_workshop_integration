# -*- coding: utf-8 -*-
"""OT nacida de una venta (30 sep 2026).

* Las placas elegidas quedan comprometidas y reservadas desde el borrador.
* Confirmar avisa a Logística (actividad) y la OT aparece en Salidas › A taller.
* No se puede iniciar mientras Logística no entregue el material.
* Al entregar, las placas salen del almacén (consumidas), se cierra el aviso y
  la OT queda confirmada lista para iniciar; iniciar arranca el reloj.
"""
from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.stone_workshop.tests.common import WorkshopCase


def _make_seller(env, login):
    # Comisiones exige que el vendedor de la venta sea usuario de ventas.
    return env['res.users'].create({
        'name': 'PRUEBA Vendedor ' + login, 'login': login,
        'group_ids': [(6, 0, [env.ref('base.group_user').id,
                              env.ref('sales_team.group_sale_salesman').id])],
    })


@tagged('post_install', '-at_install', 'sale_stone_workshop_integration')
class TestSaleWorkshopLogistics(WorkshopCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': 'PRUEBA Cliente taller'})
        cls.seller = _make_seller(cls.env, 'prueba_vendedor_taller_log')
        group = cls.env.ref('sale_delivery_wizard.group_delivery_user')
        cls.logistics_user = cls.env['res.users'].create({
            'name': 'PRUEBA Logística', 'login': 'prueba_logistica_taller',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id, group.id,
                                  cls.env.ref('stock.group_stock_user').id])],
        })
        logistics = cls.env.ref('sale_delivery_auth.group_delivery_logistics', raise_if_not_found=False)
        if logistics:
            cls.logistics_user.write({'group_ids': [(4, logistics.id)]})

    def _sale_order_with_workshop(self, lots_qty):
        so = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'user_id': self.seller.id,
            'order_line': [(0, 0, {
                'product_id': self.finished.id,
                'product_uom_qty': sum(q for _l, q in lots_qty),
                'price_unit': 100.0,
            })],
        })
        line = so.order_line
        line.with_context(skip_stone_workshop_autosync=True).write({
            'stone_workshop_required': True,
            'stone_workshop_base_product_id': self.slab.id,
            'stone_workshop_process_id': self.p_finish.id,
        })
        order = self.env['workshop.order'].create({
            'process_id': self.p_finish.id,
            'company_id': self.company.id,
            'warehouse_id': self.warehouse.id,
            'input_product_id': self.slab.id,
            'default_product_out_id': self.finished.id,
            'sale_order_id': so.id,
            'sale_line_id': line.id,
            'input_line_ids': [(0, 0, {
                'product_id': self.slab.id, 'lot_id': lot.id, 'qty_in': qty,
                'area_sqm': qty, 'location_id': self.stock_loc.id,
            }) for lot, qty in lots_qty],
        })
        return so, order

    def _activities(self, order):
        return self.env['mail.activity'].search([
            ('res_model', '=', 'workshop.order'), ('res_id', '=', order.id),
            ('summary', '=like', 'Entrega a taller%')])

    def test_01_selected_slabs_are_committed_and_reserved(self):
        lot = self.make_lot('PRB-S01', 5.0)
        _so, order = self._sale_order_with_workshop([(lot, 5.0)])
        picking = order.sale_workshop_reservation_picking_id
        self.assertTrue(picking, 'La OT de venta deja un traslado de reserva')
        self.assertIn(picking.state, ('assigned', 'confirmed'))
        committed = self.env['stock.quant']._get_committed_lot_ids(self.slab.id)
        self.assertIn(lot.id, committed, 'Placa elegida = comprometida desde borrador')
        order.action_confirm_workshop()
        committed = self.env['stock.quant']._get_committed_lot_ids(self.slab.id)
        self.assertIn(lot.id, committed, 'Sigue comprometida con la OT confirmada')

    def test_02_confirm_notifies_logistics_and_blocks_start(self):
        lot = self.make_lot('PRB-S02', 5.0)
        _so, order = self._sale_order_with_workshop([(lot, 5.0)])
        picking = order.sale_workshop_reservation_picking_id
        order.action_confirm_workshop()
        self.assertEqual(order.state, 'confirmed')
        self.assertFalse(order.timer_running)
        self.assertNotEqual(picking.state, 'done', 'Confirmar no entrega el material')
        self.assertAlmostEqual(self.qty_at(lot, self.stock_loc), 5.0, places=3)
        acts = self._activities(order)
        self.assertTrue(acts, 'Se avisa a Logística')
        self.assertIn(self.logistics_user, acts.mapped('user_id'))
        order.invalidate_recordset()
        self.assertFalse(order.material_ready)
        self.assertIn(picking.name, order.material_block_reason)
        with self.assertRaisesRegex(UserError, 'Logística'):
            order.action_start_workshop()
        self.assertEqual(order.state, 'confirmed')
        # Reconfirmar/avisar dos veces no duplica.
        order._sale_workshop_notify_logistics_delivery()
        self.assertEqual(len(self._activities(order)), len(acts))

    def test_03_outbound_board_lists_only_confirmed(self):
        lot1 = self.make_lot('PRB-S03', 5.0)
        lot2 = self.make_lot('PRB-S04', 5.0)
        _so1, draft_order = self._sale_order_with_workshop([(lot1, 5.0)])
        _so2, conf_order = self._sale_order_with_workshop([(lot2, 5.0)])
        conf_order.action_confirm_workshop()
        data = self.env['sale.delivery.live.map'].get_outbound_dashboard_data()
        ids = [c['workshop_id'] for c in data.get('to_workshop', [])]
        self.assertIn(conf_order.id, ids)
        self.assertNotIn(draft_order.id, ids, 'Un borrador no compromete a Logística')

    def test_04_logistics_delivers_then_workshop_starts(self):
        lot = self.make_lot('PRB-S05', 5.0)
        _so, order = self._sale_order_with_workshop([(lot, 5.0)])
        picking = order.sale_workshop_reservation_picking_id
        order.action_confirm_workshop()
        res = self.env['sale.delivery.live.map'].with_user(self.logistics_user).som_deliver_to_workshop(picking.id)
        self.assertTrue(res.get('ok'), res)
        self.assertEqual(picking.state, 'done')
        order.invalidate_recordset()
        self.assertEqual(order.state, 'confirmed', 'Entregar no inicia la OT')
        self.assertTrue(all(order.input_line_ids.mapped('is_consumed')))
        self.assertIn(picking, order.consume_picking_ids)
        self.assertAlmostEqual(self.qty_at(lot, self.stock_loc), 0.0, places=3,
                               msg='La placa entregada ya no está disponible en almacén')
        self.assertFalse(self._activities(order), 'El aviso a Logística se cierra solo')
        self.assertTrue(order.material_ready)
        data = self.env['sale.delivery.live.map'].get_outbound_dashboard_data()
        self.assertNotIn(order.id, [c['workshop_id'] for c in data.get('to_workshop', [])])
        order.action_start_workshop()
        self.assertEqual(order.state, 'in_workshop')
        self.assertTrue(order.timer_running)
        self.assertEqual(len(order.consume_picking_ids), 1, 'No se vuelve a mover el material')

    def test_05_print_pick_from_board(self):
        lot = self.make_lot('PRB-S06', 5.0)
        _so, order = self._sale_order_with_workshop([(lot, 5.0)])
        order.action_confirm_workshop()
        action = order.action_print_pick_report()
        self.assertEqual(action.get('type'), 'ir.actions.report')

    def test_06_unconfirm_closes_notice(self):
        lot = self.make_lot('PRB-S07', 5.0)
        _so, order = self._sale_order_with_workshop([(lot, 5.0)])
        order.action_confirm_workshop()
        self.assertTrue(self._activities(order))
        order.action_draft()
        self.assertEqual(order.state, 'draft')
        self.assertFalse(self._activities(order))

    def test_07_cancel_sale_cancels_confirmed_ot_and_releases(self):
        lot = self.make_lot('PRB-S08', 5.0)
        so, order = self._sale_order_with_workshop([(lot, 5.0)])
        order.action_confirm_workshop()
        so._stone_workshop_release_on_cancel()
        self.assertEqual(order.state, 'cancel')
        committed = self.env['stock.quant']._get_committed_lot_ids(self.slab.id)
        self.assertNotIn(lot.id, committed)

    def test_08_validate_reservation_from_inventory_also_applies(self):
        """Si Logística valida el traslado desde Inventario (no desde el tablero)."""
        lot = self.make_lot('PRB-S09', 5.0)
        _so, order = self._sale_order_with_workshop([(lot, 5.0)])
        picking = order.sale_workshop_reservation_picking_id
        order.action_confirm_workshop()
        picking.move_ids.picked = True
        picking.with_context(**order._sale_workshop_stock_context()).button_validate()
        self.assertEqual(picking.state, 'done')
        order.invalidate_recordset()
        self.assertTrue(all(order.input_line_ids.mapped('is_consumed')))
        self.assertFalse(self._activities(order))
        order.action_start_workshop()
        self.assertEqual(order.state, 'in_workshop')

    def test_09_tablet_start_waits_for_logistics(self):
        lot = self.make_lot('PRB-S10', 5.0)
        _so, order = self._sale_order_with_workshop([(lot, 5.0)])
        detail = order.tablet_start()
        self.assertEqual(order.state, 'confirmed', 'Sin material entregado queda confirmada')
        self.assertFalse(order.timer_running)
        self.assertFalse(detail['can_start'])
        self.assertTrue(detail['start_block_reason'])
        self.assertTrue(self._activities(order))

    def test_10_chain_finish_then_cut_end_to_end(self):
        """Pulido (paso 1) → Corte (paso 2) de la misma venta."""
        lot = self.make_lot('PRB-S11', 6.0)
        so, step1 = self._sale_order_with_workshop([(lot, 6.0)])
        step2 = self.env['workshop.order'].create({
            'process_id': self.p_cut.id,
            'company_id': self.company.id,
            'warehouse_id': self.warehouse.id,
            'input_product_id': self.finished.id,
            'default_product_out_id': self.cut.id,
            'sale_order_id': so.id,
            'sale_line_id': step1.sale_line_id.id,
            'stone_workshop_chain_sequence': 2,
            'stone_workshop_chain_prev_order_id': step1.id,
        })
        step1.stone_workshop_chain_next_order_id = step2.id
        # El corte no se confirma mientras el pulido no entregue nada.
        with self.assertRaisesRegex(UserError, 'paso anterior'):
            step2.action_confirm_workshop()
        # Pulido: Logística entrega, taller inicia, trabaja y cierra.
        step1.action_confirm_workshop()
        self.env['sale.delivery.live.map'].som_deliver_to_workshop(
            step1.sale_workshop_reservation_picking_id.id)
        step1.action_start_workshop()
        self.log(step1, [(step1.input_line_ids, 6.0)], 6.0)
        step1.action_declare_result()
        self.assertEqual(step1.state, 'done')
        # El corte recibe el material pulido como entrada.
        step2.invalidate_recordset()
        fed = step2.input_line_ids.filtered(lambda l: l.state != 'cancelled')
        self.assertTrue(fed, 'El paso 2 se alimenta con lo que produjo el paso 1')
        self.assertEqual(fed.mapped('product_id'), self.finished)
        step2.action_confirm_workshop()
        self.assertEqual(step2.state, 'confirmed')
        if step2.sale_workshop_reservation_picking_id and \
                step2.sale_workshop_reservation_picking_id.state != 'done':
            self.assertFalse(step2.material_ready)
            self.env['sale.delivery.live.map'].som_deliver_to_workshop(
                step2.sale_workshop_reservation_picking_id.id)
        step2.action_start_workshop()
        self.assertEqual(step2.state, 'in_workshop')
        self.log(step2, [(fed[:1], 6.0)], 5.0)
        step2.action_declare_result()
        self.assertEqual(step2.state, 'done')
