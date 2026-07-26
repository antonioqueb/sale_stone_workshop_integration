# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class SaleStoneWorkshopProcessLine(models.Model):
    _name = 'sale.stone.workshop.process.line'
    _description = 'Proceso adicional de taller encadenado desde venta'
    _order = 'sale_line_id, sequence, id'
    _rec_name = 'name'

    sequence = fields.Integer(default=10)

    sale_line_id = fields.Many2one(
        'sale.order.line',
        string='Línea de venta',
        required=True,
        ondelete='cascade',
        index=True,
    )
    sale_order_id = fields.Many2one(
        related='sale_line_id.order_id',
        string='Orden de venta',
        store=True,
        readonly=True,
        index=True,
    )
    company_id = fields.Many2one(
        related='sale_line_id.company_id',
        string='Compañía',
        store=True,
        readonly=True,
    )
    process_id = fields.Many2one(
        'workshop.process',
        string='Proceso',
        required=True,
        help='Servicio de taller que se aplica en este paso de la cadena.',
    )
    input_product_id = fields.Many2one(
        'product.product',
        string='Recibe',
        required=True,
        domain=[('tracking', '!=', 'none')],
        help=(
            'Producto intermedio que este paso recibe del paso anterior. '
            'Lo que este paso entrega lo define el siguiente paso (su "Recibe"); '
            'el último paso entrega el producto vendido.'
        ),
    )
    output_product_display = fields.Char(
        string='Entrega',
        compute='_compute_output_product_display',
        help='Lo que este paso produce: el producto que recibe el siguiente '
             'paso, o el producto vendido si es el último de la cadena.',
    )
    workshop_order_id = fields.Many2one(
        'workshop.order',
        string='Orden de taller',
        readonly=True,
        copy=False,
        ondelete='set null',
        help='Orden de taller generada para este paso al confirmar la venta.',
    )
    workshop_order_state = fields.Selection(
        related='workshop_order_id.state',
        string='Estado OT',
        readonly=True,
    )
    name = fields.Char(
        string='Descripción',
        compute='_compute_name',
        store=True,
    )

    @api.depends('process_id', 'input_product_id')
    def _compute_name(self):
        for line in self:
            process = line.process_id.display_name or ''
            product = line.input_product_id.display_name or ''
            if process and product:
                line.name = '%s ← %s' % (process, product)
            else:
                line.name = process or product or '/'

    @api.depends(
        'sequence',
        'input_product_id',
        'sale_line_id.product_id',
        'sale_line_id.stone_workshop_process_line_ids.sequence',
        'sale_line_id.stone_workshop_process_line_ids.input_product_id',
    )
    def _compute_output_product_display(self):
        """Lo que entrega el paso = lo que recibe el paso siguiente.

        Orden estable SOLO por sequence: en edición dentro del modal las
        líneas pueden ser NewId y no admiten desempatar por id.
        """
        for line in self:
            sale_line = line.sale_line_id
            if not sale_line:
                line.output_product_display = ''
                continue

            siblings = list(sale_line.stone_workshop_process_line_ids.sorted(
                key=lambda l: l.sequence or 0
            ))
            try:
                position = siblings.index(line)
            except ValueError:
                line.output_product_display = ''
                continue

            if position + 1 < len(siblings):
                next_product = siblings[position + 1].input_product_id
                line.output_product_display = (
                    next_product.display_name
                    if next_product
                    else _('Sin definir (paso siguiente)')
                )
            else:
                final_product = sale_line.product_id
                line.output_product_display = (
                    _('%s (producto vendido)') % final_product.display_name
                    if final_product
                    else _('Producto vendido')
                )

    # -------------------------------------------------------------------------
    # Sincronización de la cadena de OTs al editar pasos tras la confirmación
    # -------------------------------------------------------------------------

    _CHAIN_SYNC_FIELDS = ('process_id', 'input_product_id', 'sequence')

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        # Paso agregado con la venta YA confirmada: crear su OT y re-enlazar
        # la cadena de inmediato (antes solo nacían al confirmar la venta).
        if not self.env.context.get('skip_stone_workshop_chain_resync'):
            lines.mapped('sale_line_id')._stone_workshop_resync_chain()
        return lines

    def write(self, vals):
        res = super().write(vals)
        if (
            not self.env.context.get('skip_stone_workshop_chain_resync')
            and any(f in vals for f in self._CHAIN_SYNC_FIELDS)
        ):
            self.mapped('sale_line_id')._stone_workshop_resync_chain()
        return res

    def unlink(self):
        """Un paso con OT viva no se borra silenciosamente.

        Borrar la fila dejaría la OT del paso huérfana (ondelete='set null')
        y la cadena rota sin aviso. Se exige cancelar la OT primero. Tras el
        borrado se re-enlaza la cadena restante.
        """
        linked = self.filtered(
            lambda l: l.workshop_order_id and l.workshop_order_id.state != 'cancel'
        )
        if linked:
            raise UserError(_(
                'No puedes eliminar estos pasos porque ya tienen orden de taller '
                'generada:\n%s\n\nCancela primero la orden de taller correspondiente.'
            ) % '\n'.join(
                '- %s → %s' % (l.name, l.workshop_order_id.name) for l in linked
            ))

        sale_lines = self.mapped('sale_line_id')
        res = super().unlink()

        if not self.env.context.get('skip_stone_workshop_chain_resync'):
            sale_lines.exists()._stone_workshop_resync_chain()

        return res
