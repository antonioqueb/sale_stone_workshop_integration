# -*- coding: utf-8 -*-
"""Regla de taller: primero acabados, al final corte/formato (30 sep 2026)."""
from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.stone_workshop.tests.common import WorkshopCase


@tagged('post_install', '-at_install', 'sale_stone_workshop_integration')
class TestWorkshopChainOrder(WorkshopCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.p_format = cls.env['workshop.process'].create({'name': 'PRUEBA Formato', 'process_type': 'format'})
        cls.p_rework = cls.env['workshop.process'].create({'name': 'PRUEBA Reproceso', 'process_type': 'rework'})
        cls.partner = cls.env['res.partner'].create({'name': 'PRUEBA Cliente cadena'})
        Product = cls.env['product.product']
        cls.polished = Product.create({'name': 'PRUEBA intermedio pulido', 'is_storable': True,
                                       'tracking': 'lot', 'uom_id': cls.uom_m2.id})

    def _line(self, main_process):
        so = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'order_line': [(0, 0, {'product_id': self.cut.id, 'product_uom_qty': 10, 'price_unit': 1})],
        })
        line = so.order_line
        line.with_context(skip_stone_workshop_autosync=True).write({
            'stone_workshop_required': True,
            'stone_workshop_base_product_id': self.slab.id,
            'stone_workshop_process_id': main_process.id,
        })
        return line

    def test_01_order_rule_helper(self):
        SOL = self.env['sale.order.line']
        self.assertFalse(SOL._workshop_chain_order_errors([self.p_finish, self.p_cut]))
        self.assertFalse(SOL._workshop_chain_order_errors([self.p_finish, self.p_rework, self.p_cut, self.p_format]))
        self.assertTrue(SOL._workshop_chain_order_errors([self.p_cut, self.p_finish]))
        self.assertTrue(SOL._workshop_chain_order_errors([self.p_format, self.p_cut]))
        self.assertTrue(SOL._workshop_chain_order_errors([self.p_finish, self.p_cut, self.p_rework]))

    def test_02_save_rejects_cut_before_finish(self):
        line = self._line(self.p_cut)
        with self.assertRaisesRegex(UserError, 'acabados'):
            line.save_workshop_chain_from_workspace({'steps': [
                {'id': 0, 'type': 'main', 'process_id': self.p_cut.id, 'input_product_id': False},
                {'id': 0, 'type': 'extra', 'process_id': self.p_finish.id,
                 'input_product_id': self.polished.id},
            ]})

    def test_03_save_valid_chain(self):
        line = self._line(self.p_finish)
        line.save_workshop_chain_from_workspace({'steps': [
            {'id': 0, 'type': 'main', 'process_id': self.p_finish.id, 'input_product_id': False},
            {'id': 0, 'type': 'extra', 'process_id': self.p_cut.id, 'input_product_id': self.polished.id},
        ]})
        steps = line._stone_workshop_chain_steps()
        self.assertEqual([s['process'] for s in steps], [self.p_finish, self.p_cut])

    def test_04_seller_captured_cut_first_then_polish(self):
        """El vendedor puso Corte como proceso principal y agregó Pulido después:
        el asistente sube el Pulido (renglón adicional) al paso 1 y al guardar
        el principal pasa a ser Pulido y el Corte baja como paso adicional."""
        line = self._line(self.p_cut)
        pl = self.env['sale.stone.workshop.process.line'].with_context(
            skip_stone_workshop_chain_resync=True).create({
                'sale_line_id': line.id, 'sequence': 10,
                'process_id': self.p_finish.id, 'input_product_id': self.polished.id})
        data = line.get_workshop_chain_workspace_data()
        self.assertTrue(data.get('order_rule'))
        ranks = [s['process']['rank'] for s in data['steps']]
        self.assertEqual(ranks, [1, 0], 'Así llega la cadena mal capturada')
        # Lo que envía el asistente ya reordenado: pulido (renglón pl) primero.
        line.save_workshop_chain_from_workspace({'steps': [
            {'id': pl.id, 'type': 'extra', 'process_id': self.p_finish.id, 'input_product_id': False},
            {'id': 0, 'type': 'main', 'process_id': self.p_cut.id, 'input_product_id': self.polished.id},
        ], 'deleted_ids': []})
        self.assertEqual(line.stone_workshop_process_id, self.p_finish)
        steps = line._stone_workshop_chain_steps()
        self.assertEqual([s['process'] for s in steps], [self.p_finish, self.p_cut])
        self.assertEqual(steps[1]['input_product'], self.polished)
        self.assertFalse(pl.exists(), 'El renglón promovido se elimina')

    def test_05_processes_catalog_sorted_finish_first(self):
        line = self._line(self.p_finish)
        data = line.get_workshop_chain_workspace_data()
        ranks = [p['rank'] for p in data['processes']]
        self.assertEqual(ranks, sorted(ranks))
