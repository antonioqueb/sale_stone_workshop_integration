# -*- coding: utf-8 -*-
import json
import logging
from html import escape

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_compare

from .product import (
    WORKSHOP_OPERATION_MODE_SELECTION,
    WORKSHOP_TRIGGER_SELECTION,
    WORKSHOP_COMMERCIAL_MODE_SELECTION,
)

_logger = logging.getLogger(__name__)


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    stone_workshop_required = fields.Boolean(
        string='Requiere taller',
        copy=True,
        help='Indica que esta línea vende un producto final que debe producirse o transformarse en taller.',
    )
    stone_workshop_auto_create = fields.Boolean(
        string='Crear OT automática',
        default=True,
        copy=True,
    )
    stone_workshop_base_product_id = fields.Many2one(
        'product.product',
        string='Producto base',
        domain=[('tracking', '!=', 'none')],
        copy=True,
        help='Producto que se aparta/consume para fabricar el producto vendido en esta línea.',
    )
    stone_workshop_process_id = fields.Many2one(
        'workshop.process',
        string='Proceso taller',
        copy=True,
    )
    stone_workshop_operation_mode = fields.Selection(
        WORKSHOP_OPERATION_MODE_SELECTION,
        string='Modo taller',
        default='slab_finish',
        copy=True,
    )
    stone_workshop_trigger = fields.Selection(
        WORKSHOP_TRIGGER_SELECTION,
        string='Disparador taller',
        default='on_shortage',
        copy=True,
    )
    stone_workshop_commercial_mode = fields.Selection(
        WORKSHOP_COMMERCIAL_MODE_SELECTION,
        string='Modo comercial taller',
        default='single_line',
        copy=True,
    )
    stone_workshop_service_product_id = fields.Many2one(
        'product.product',
        string='Servicio taller sugerido',
        domain=[('type', '=', 'service')],
        copy=True,
    )
    stone_workshop_order_id = fields.Many2one(
        'workshop.order',
        string='Orden de taller',
        copy=False,
        readonly=True,
        ondelete='set null',
    )
    stone_workshop_state = fields.Selection(
        related='stone_workshop_order_id.state',
        string='Estado taller',
        readonly=True,
        store=True,
    )
    stone_workshop_assignment_state = fields.Selection([
        ('none', 'Sin taller'),
        ('pending_inputs', 'Pendiente placas base'),
        ('reserved_inputs', 'Placas base reservadas'),
        ('in_workshop', 'En taller'),
        ('outputs_pending', 'Salida pendiente'),
        ('assigned', 'Producto final asignado'),
        ('cancelled', 'Cancelado'),
    ], string='Asignación taller', compute='_compute_stone_workshop_status', store=True)

    stone_workshop_input_lot_ids = fields.Many2many(
        'stock.lot',
        'sale_line_workshop_input_lot_rel',
        'sale_line_id',
        'lot_id',
        string='Lotes base taller',
        compute='_compute_stone_workshop_lots',
        store=False,
    )
    stone_workshop_output_lot_ids = fields.Many2many(
        'stock.lot',
        'sale_line_workshop_output_lot_rel',
        'sale_line_id',
        'lot_id',
        string='Lotes finales taller',
        compute='_compute_stone_workshop_lots',
        store=False,
    )
    stone_workshop_input_count = fields.Integer(
        string='Placas base',
        compute='_compute_stone_workshop_lots',
        store=False,
    )
    stone_workshop_output_count = fields.Integer(
        string='Lotes finales',
        compute='_compute_stone_workshop_lots',
        store=False,
    )

    stone_workshop_input_selection_ids = fields.One2many(
        'sale.stone.workshop.input.selection',
        'sale_line_id',
        string='Placas base a consumir',
        copy=False,
    )
    stone_workshop_input_selector_anchor = fields.Boolean(
        string='Selector placas base',
        compute='_compute_stone_workshop_input_selection_summary',
    )
    stone_workshop_input_selection_count = fields.Integer(
        string='Placas base seleccionadas',
        compute='_compute_stone_workshop_input_selection_summary',
    )
    stone_workshop_input_selection_total_qty = fields.Float(
        string='Total base seleccionado',
        compute='_compute_stone_workshop_input_selection_summary',
        digits=(12, 4),
    )

    stone_workshop_process_line_ids = fields.One2many(
        'sale.stone.workshop.process.line',
        'sale_line_id',
        string='Procesos adicionales',
        copy=True,
        help=(
            'Cadena de procesos de taller adicionales que se aplican después del '
            'proceso principal. Cada paso consume el producto intermedio que produce '
            'el paso anterior; el último paso produce el producto vendido.'
        ),
    )
    stone_workshop_process_chain_count = fields.Integer(
        string='Procesos adicionales',
        compute='_compute_stone_workshop_process_chain_count',
    )
    stone_workshop_chain_preview = fields.Html(
        string='Vista de la cadena',
        compute='_compute_stone_workshop_chain_preview',
        sanitize=False,
        help='Representación visual de la cadena de procesos: producto origen, '
             'pasos de taller (con su OT si ya existe) y producto vendido. '
             'Contenido generado y escapado en servidor; solo lectura.',
    )

    stone_is_workshop_service_line = fields.Boolean(
        string='Línea de servicio taller',
        copy=False,
        help='Marca técnica para una línea de servicio comercial relacionada con taller.',
    )
    stone_workshop_parent_line_id = fields.Many2one(
        'sale.order.line',
        string='Línea producto taller',
        copy=False,
        ondelete='set null',
    )
    stone_workshop_hide_from_customer = fields.Boolean(
        string='Ocultar al cliente',
        copy=False,
        help='Campo técnico para reportes personalizados. El integrador no altera reportes fiscales estándar.',
    )

    def _stone_workshop_chain_orders(self):
        """Cadena completa de OTs de la línea, en orden de ejecución.

        La línea solo guarda la PRIMERA orden (`stone_workshop_order_id`);
        los pasos siguientes se recorren vía `stone_workshop_chain_next_order_id`.
        Con guarda anti-ciclos por si un enlace quedó corrupto.
        """
        self.ensure_one()
        chain = self.env['workshop.order']
        order = self.stone_workshop_order_id
        guard = 0
        while order and order.id not in chain.ids and guard < 50:
            chain |= order
            order = order.stone_workshop_chain_next_order_id
            guard += 1
        return chain

    @api.depends(
        'stone_workshop_required',
        'stone_workshop_order_id.state',
        'stone_workshop_order_id.input_line_ids.state',
        'stone_workshop_order_id.output_line_ids.state',
        'stone_workshop_order_id.output_line_ids.lot_id',
        'stone_workshop_order_id.stone_workshop_chain_next_order_id.state',
        'stone_workshop_input_selection_ids.state',
        'lot_ids',
    )
    def _compute_stone_workshop_status(self):
        for line in self:
            if not line.stone_workshop_required:
                line.stone_workshop_assignment_state = 'none'
                continue

            active_selections = line.stone_workshop_input_selection_ids.filtered(
                lambda s: s.state != 'cancelled'
            )

            if not line.stone_workshop_order_id:
                line.stone_workshop_assignment_state = 'reserved_inputs' if active_selections else 'pending_inputs'
                continue

            # Con cadena de procesos, el producto final lo produce la ÚLTIMA
            # orden, no la primera: el estado se evalúa sobre toda la cadena.
            chain = line._stone_workshop_chain_orders()
            active_chain = chain.filtered(lambda o: o.state != 'cancel')

            if not active_chain:
                line.stone_workshop_assignment_state = 'cancelled'
                continue

            first_order = active_chain[0]
            final_order = active_chain[-1]

            input_lines = first_order.input_line_ids.filtered(lambda l: l.state != 'cancelled')
            chain_outputs = active_chain.mapped('output_line_ids').filtered(
                lambda l: l.state != 'cancelled'
            )

            final_output_lots = final_order.output_line_ids.filtered(
                lambda o:
                    o.state != 'cancelled'
                    and o.output_type not in ('scrap', 'rejected')
                    and o.product_id == line.product_id
                    and o.lot_id
            ).mapped('lot_id')

            if final_output_lots and set(final_output_lots.ids).issubset(set(line.lot_ids.ids)):
                line.stone_workshop_assignment_state = 'assigned'
            elif any(o.state == 'in_workshop' for o in active_chain):
                line.stone_workshop_assignment_state = 'in_workshop'
            elif chain_outputs:
                line.stone_workshop_assignment_state = 'outputs_pending'
            elif input_lines.filtered(lambda l: l.state == 'reserved_for_workshop') or active_selections:
                line.stone_workshop_assignment_state = 'reserved_inputs'
            else:
                line.stone_workshop_assignment_state = 'pending_inputs'

    @api.depends(
        'stone_workshop_order_id.input_line_ids.lot_id',
        'stone_workshop_order_id.output_line_ids.lot_id',
        'stone_workshop_order_id.input_line_ids.state',
        'stone_workshop_order_id.output_line_ids.state',
        'stone_workshop_input_selection_ids.lot_id',
        'stone_workshop_input_selection_ids.state',
    )
    def _compute_stone_workshop_lots(self):
        for line in self:
            order = line.stone_workshop_order_id
            input_lots = self.env['stock.lot']
            output_lots = self.env['stock.lot']

            if order:
                # Entradas: primera orden de la cadena (consume el producto base).
                # Salidas finales: última orden activa (produce el producto vendido).
                chain = line._stone_workshop_chain_orders()
                active_chain = chain.filtered(lambda o: o.state != 'cancel')
                first_order = active_chain[0] if active_chain else order
                final_order = active_chain[-1] if active_chain else order

                input_lots = first_order.input_line_ids.filtered(
                    lambda l: l.state != 'cancelled' and l.lot_id
                ).mapped('lot_id')
                output_lots = final_order.output_line_ids.filtered(
                    lambda l:
                        l.state != 'cancelled'
                        and l.output_type not in ('scrap', 'rejected')
                        and l.lot_id
                ).mapped('lot_id')

            if not input_lots:
                input_lots = line.stone_workshop_input_selection_ids.filtered(
                    lambda s: s.state != 'cancelled' and s.lot_id
                ).mapped('lot_id')

            line.stone_workshop_input_lot_ids = input_lots
            line.stone_workshop_output_lot_ids = output_lots
            line.stone_workshop_input_count = len(input_lots)
            line.stone_workshop_output_count = len(output_lots)

    @api.depends(
        'stone_workshop_required',
        'stone_workshop_base_product_id',
        'stone_workshop_input_selection_ids.qty_in',
        'stone_workshop_input_selection_ids.state',
    )
    def _compute_stone_workshop_input_selection_summary(self):
        for line in self:
            selections = line.stone_workshop_input_selection_ids.filtered(
                lambda s: s.state != 'cancelled'
            )
            line.stone_workshop_input_selector_anchor = bool(
                line.stone_workshop_required and line.stone_workshop_base_product_id
            )
            line.stone_workshop_input_selection_count = len(selections)
            line.stone_workshop_input_selection_total_qty = sum(selections.mapped('qty_in'))

    @api.depends('stone_workshop_process_line_ids')
    def _compute_stone_workshop_process_chain_count(self):
        for line in self:
            line.stone_workshop_process_chain_count = len(line.stone_workshop_process_line_ids)

    @api.depends(
        'stone_workshop_required',
        'product_id',
        'stone_workshop_base_product_id',
        'stone_workshop_process_id',
        'stone_workshop_order_id.state',
        'stone_workshop_process_line_ids.sequence',
        'stone_workshop_process_line_ids.process_id',
        'stone_workshop_process_line_ids.input_product_id',
        'stone_workshop_process_line_ids.workshop_order_id.state',
    )
    def _compute_stone_workshop_chain_preview(self):
        """Renderiza la cadena como pipeline visual (HTML de servidor, escapado).

        Se recalcula en vivo dentro del modal al agregar/quitar/reordenar
        pasos, para que el usuario vea de inmediato qué recibe y qué entrega
        cada proceso. Ordena las líneas SOLO por sequence (orden estable):
        en edición los registros pueden ser NewId y no admiten ordenar por id.
        """
        state_labels = dict(self.env['workshop.order']._fields['state'].selection)
        state_badges = {
            'draft': 'text-bg-secondary',
            'in_workshop': 'text-bg-warning',
            'done': 'text-bg-success',
            'cancel': 'text-bg-danger',
        }

        def chip(product, kind, tag):
            if product:
                return (
                    '<span class="o_sw_chain_material o_sw_%s">'
                    '<span class="o_sw_tag">%s</span>%s</span>'
                ) % (kind, escape(tag), escape(product.display_name or ''))
            return (
                '<span class="o_sw_chain_material o_sw_missing">'
                '<span class="o_sw_tag">%s</span>%s</span>'
            ) % (escape(tag), escape(_('Sin definir')))

        arrow = (
            '<span class="o_sw_chain_arrow">'
            '<i class="fa fa-long-arrow-right" role="img" aria-label="&#8594;" title=""/>'
            '</span>'
        )

        for line in self:
            if (
                not line.stone_workshop_required
                or line.display_type
                or line.stone_is_workshop_service_line
            ):
                line.stone_workshop_chain_preview = False
                continue

            steps = [{
                'label': _('Paso 1 · Principal'),
                'process': line.stone_workshop_process_id,
                'input_product': line.stone_workshop_base_product_id,
                'workshop': line.stone_workshop_order_id,
            }]
            extra_lines = line.stone_workshop_process_line_ids.sorted(
                key=lambda l: l.sequence or 0
            )
            for index, process_line in enumerate(extra_lines, start=2):
                steps.append({
                    'label': _('Paso %s') % index,
                    'process': process_line.process_id,
                    'input_product': process_line.input_product_id,
                    'workshop': process_line.workshop_order_id,
                })

            for index, step in enumerate(steps):
                if index + 1 < len(steps):
                    step['output_product'] = steps[index + 1]['input_product']
                else:
                    step['output_product'] = line.product_id

            parts = ['<div class="o_sw_chain">']
            parts.append(chip(line.stone_workshop_base_product_id, 'origin', _('Origen')))

            for index, step in enumerate(steps):
                parts.append(arrow)

                process = step['process']
                process_name = (
                    escape(process.display_name or '')
                    if process
                    else escape(_('Sin proceso'))
                )
                step_class = 'o_sw_chain_step' + ('' if process else ' o_sw_step_missing')

                ot_html = ''
                workshop = step['workshop']
                if workshop and workshop.id:
                    badge_class = state_badges.get(workshop.state, 'text-bg-secondary')
                    ot_html = (
                        '<div class="o_sw_step_ot"><span class="badge %s">%s · %s</span></div>'
                    ) % (
                        badge_class,
                        escape(workshop.name or ''),
                        escape(state_labels.get(workshop.state, workshop.state or '')),
                    )

                parts.append(
                    '<span class="%s">'
                    '<div class="o_sw_step_seq">%s</div>'
                    '<div class="o_sw_step_name">%s</div>'
                    '%s</span>' % (step_class, escape(step['label']), process_name, ot_html)
                )

                parts.append(arrow)

                is_final = index + 1 == len(steps)
                parts.append(chip(
                    step['output_product'],
                    'final' if is_final else 'intermediate',
                    _('Vendido') if is_final else _('Intermedio'),
                ))

            parts.append('</div>')
            line.stone_workshop_chain_preview = ''.join(parts)

    # -------------------------------------------------------------------------
    # Cadena de procesos adicionales de taller
    # -------------------------------------------------------------------------

    def _stone_workshop_chain_steps(self):
        """Devuelve la lista ordenada de pasos de la cadena de taller.

        Cada paso es un dict con: process, input_product, output_product,
        sequence y process_line (vacío en el paso principal). El producto de
        salida de cada paso es el producto que consume el siguiente paso, y el
        último paso produce el producto vendido (``product_id``).
        """
        self.ensure_one()

        steps = []

        # Paso 1: proceso principal de la línea (consume el producto origen).
        steps.append({
            'process': self.stone_workshop_process_id,
            'input_product': self.stone_workshop_base_product_id,
            'process_line': self.env['sale.stone.workshop.process.line'],
        })

        # Pasos adicionales, ordenados por secuencia.
        additional = self.stone_workshop_process_line_ids.sorted(
            key=lambda l: (l.sequence, l.id)
        )
        for process_line in additional:
            steps.append({
                'process': process_line.process_id,
                'input_product': process_line.input_product_id,
                'process_line': process_line,
            })

        # El producto de salida de cada paso es la entrada del siguiente; el
        # último paso produce el producto vendido.
        for index, step in enumerate(steps):
            step['sequence'] = index + 1
            if index + 1 < len(steps):
                step['output_product'] = steps[index + 1]['input_product']
            else:
                step['output_product'] = self.product_id

        return steps

    def action_open_stone_workshop_process_chain(self):
        self.ensure_one()

        if not self.id:
            raise UserError(_('Guarda la línea antes de configurar procesos adicionales.'))
        if self.display_type or self.stone_is_workshop_service_line:
            raise UserError(_('Esta línea no admite procesos de taller.'))

        return {
            'type': 'ir.actions.act_window',
            'name': _('Cadena de procesos de taller'),
            'res_model': 'sale.order.line',
            'res_id': self.id,
            'view_mode': 'form',
            'view_id': self.env.ref(
                'sale_stone_workshop_integration.view_sale_order_line_workshop_chain_form'
            ).id,
            'target': 'new',
            'context': dict(self.env.context, dialog_size='extra-large'),
        }

    @api.model
    def _stone_workshop_vals_from_product(self, product):
        if not product or not product.exists() or not product.stone_workshop_required:
            return {
                'stone_workshop_required': False,
                'stone_workshop_base_product_id': False,
                'stone_workshop_process_id': False,
                'stone_workshop_operation_mode': 'slab_finish',
                'stone_workshop_trigger': 'on_shortage',
                'stone_workshop_auto_create': True,
                'stone_workshop_commercial_mode': 'single_line',
                'stone_workshop_service_product_id': False,
            }

        return {
            'stone_workshop_required': True,
            'stone_workshop_base_product_id': product.stone_workshop_base_product_id.id or False,
            'stone_workshop_process_id': product.stone_workshop_process_id.id or False,
            'stone_workshop_operation_mode': product.stone_workshop_operation_mode or 'slab_finish',
            'stone_workshop_trigger': product.stone_workshop_trigger or 'on_shortage',
            'stone_workshop_auto_create': product.stone_workshop_auto_create,
            'stone_workshop_commercial_mode': product.stone_workshop_commercial_mode or 'single_line',
            'stone_workshop_service_product_id': product.stone_workshop_service_product_id.id or False,
        }

    @api.onchange('product_id')
    def _onchange_stone_workshop_product_id(self):
        for line in self:
            if line.display_type or line.stone_is_workshop_service_line:
                continue

            vals = line._stone_workshop_vals_from_product(line.product_id)
            for key, value in vals.items():
                line[key] = value

    @api.model
    def _stone_workshop_prepare_create_vals(self, vals):
        clean = dict(vals or {})
        product_id = clean.get('product_id')

        if not product_id or clean.get('display_type') or clean.get('stone_is_workshop_service_line'):
            return clean

        try:
            product = self.env['product.product'].browse(int(product_id)).exists()
        except (TypeError, ValueError):
            product = self.env['product.product']

        if not product:
            return clean

        product_vals = self._stone_workshop_vals_from_product(product)

        for key, value in product_vals.items():
            clean.setdefault(key, value)

        return clean

    # Campos cuyo cambio debe disparar la creación/sincronización automática
    # de la OT en órdenes ya confirmadas.
    _STONE_WORKSHOP_SYNC_FIELDS = (
        'product_id',
        'product_uom_qty',
        'stone_workshop_required',
        'stone_workshop_base_product_id',
        'stone_workshop_process_id',
        'stone_workshop_operation_mode',
        'stone_workshop_auto_create',
        'stone_workshop_trigger',
    )

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [self._stone_workshop_prepare_create_vals(vals) for vals in vals_list]
        lines = super().create(vals_list)
        # Línea agregada a una orden YA confirmada: la OT nace sola en cuanto
        # la línea tiene todo (sin botón manual). En cotización no hace nada.
        lines._stone_workshop_autosync()
        return lines

    def write(self, vals):
        vals = dict(vals or {})

        if (
            'product_id' in vals
            and not self.env.context.get('skip_stone_workshop_product_defaults')
            and len(self) == 1
            and not self.display_type
            and not self.stone_is_workshop_service_line
        ):
            try:
                product = self.env['product.product'].browse(int(vals.get('product_id'))).exists()
            except (TypeError, ValueError):
                product = self.env['product.product']

            if product:
                defaults = self._stone_workshop_vals_from_product(product)
                for key, value in defaults.items():
                    vals.setdefault(key, value)

        res = super().write(vals)

        if any(f in vals for f in self._STONE_WORKSHOP_SYNC_FIELDS):
            self._stone_workshop_autosync()

        return res

    def unlink(self):
        """Al borrar la línea no deben quedar OTs huérfanas reservando stock.

        `workshop.order.sale_line_id` es ondelete='set null': sin este guard,
        borrar la línea dejaba la OT viva con su picking de reserva bloqueando
        las placas para siempre. OTs en borrador se cancelan (libera reserva y
        selecciones); OTs en taller bloquean el borrado.
        """
        real_lines = self.filtered(lambda l: not l.display_type)
        if real_lines:
            workshops = self.env['workshop.order'].search([
                ('sale_line_id', 'in', real_lines.ids),
                ('state', 'in', ('draft', 'in_workshop')),
            ])
            blocking = workshops.filtered(lambda o: o.state == 'in_workshop')
            if blocking:
                raise UserError(_(
                    'No puedes eliminar la línea porque tiene órdenes de taller en '
                    'proceso con material consumido: %s.'
                ) % ', '.join(blocking.mapped('name')))

            draft_workshops = workshops - blocking
            if draft_workshops:
                draft_workshops.action_cancel()

        return super().unlink()

    # -------------------------------------------------------------------------
    # Auto-creación / sincronización de OT en órdenes confirmadas
    # -------------------------------------------------------------------------

    def _stone_workshop_autosync(self):
        """En órdenes YA CONFIRMADAS, sin botón manual:

        - Si la línea aún no tiene OT y ya reúne todo lo necesario (requiere
          taller + producto base + proceso), la OT se crea automáticamente en
          ese momento — cubre el caso de agregar el servicio de taller DESPUÉS
          de confirmar la venta (el trigger original solo corría en
          action_confirm).
        - Si ya tiene OT, se sincronizan los datos editables mientras la OT
          siga en borrador (cantidad objetivo, proceso, producto base/final).

        En cotizaciones no hace nada: la OT sigue naciendo al confirmar.
        """
        if self.env.context.get('skip_stone_workshop_autosync'):
            return

        for line in self:
            order = line.order_id
            if not order or order.state not in ('sale', 'done'):
                continue
            if 'x_is_quote_backup' in order._fields and order.x_is_quote_backup:
                continue
            if line.display_type or line.stone_is_workshop_service_line:
                continue

            if line.stone_workshop_order_id and line.stone_workshop_order_id.state != 'cancel':
                line._stone_workshop_sync_existing_order()
                continue

            reason = order._stone_workshop_line_skip_reason(line, manual=True)
            if reason:
                _logger.info(
                    '[STONE WORKSHOP SALE] Autosync: línea %s aún sin OT (%s)',
                    line.id,
                    reason,
                )
                continue

            _logger.info(
                '[STONE WORKSHOP SALE] Autosync: creando OT para línea %s '
                'agregada tras la confirmación de %s',
                line.id,
                order.name,
            )
            order.with_context(
                skip_stone_workshop_autosync=True,
            )._stone_workshop_create_missing_orders(force_lines=line)

    def _stone_workshop_sync_existing_order(self):
        """Mantiene la OT en borrador alineada con la línea de venta."""
        self.ensure_one()
        workshop_order = self.stone_workshop_order_id

        if not workshop_order or workshop_order.state != 'draft':
            return

        vals = {}

        target_vals = self._stone_workshop_production_target_vals()
        target = target_vals.get('production_target_sqm') or 0.0
        if abs((workshop_order.production_target_sqm or 0.0) - target) > 0.0001:
            vals['production_target_sqm'] = target

        target_pieces = target_vals.get('target_pieces') or 1
        if (workshop_order.target_pieces or 1) != target_pieces:
            vals['target_pieces'] = target_pieces

        process = self.stone_workshop_process_id
        if process and workshop_order.process_id != process:
            vals['process_id'] = process.id

        base_product = self.stone_workshop_base_product_id
        if base_product and workshop_order.input_product_id != base_product:
            vals['input_product_id'] = base_product.id

        if (
            self.product_id
            and 'default_product_out_id' in workshop_order._fields
            and workshop_order.default_product_out_id != self.product_id
        ):
            vals['default_product_out_id'] = self.product_id.id

        if vals:
            _logger.info(
                '[STONE WORKSHOP SALE] Autosync: OT %s actualizada desde la '
                'línea %s: %s',
                workshop_order.display_name,
                self.id,
                list(vals.keys()),
            )
            workshop_order.write(vals)

    def _stone_workshop_line_qty_done_or_reserved(self):
        self.ensure_one()
        qty = 0.0

        for move in self.move_ids.filtered(lambda m: m.state not in ('cancel',)):
            for ml in move.move_line_ids:
                if ml.product_id != self.product_id:
                    continue

                if 'quantity' in ml._fields:
                    qty += ml.quantity or 0.0
                elif 'reserved_uom_qty' in ml._fields:
                    qty += ml.reserved_uom_qty or 0.0
                elif 'qty_done' in ml._fields:
                    qty += ml.qty_done or 0.0

        return qty

    def _stone_workshop_final_available_qty(self):
        self.ensure_one()

        if not self.product_id:
            return 0.0

        domain = [
            ('product_id', '=', self.product_id.id),
            ('location_id.usage', '=', 'internal'),
            ('quantity', '>', 0),
        ]

        warehouse = self.order_id.warehouse_id if self.order_id else False
        if warehouse and warehouse.lot_stock_id:
            domain.append(('location_id', 'child_of', warehouse.lot_stock_id.id))

        qty = 0.0
        for quant in self.env['stock.quant'].search(domain):
            reserved = quant.reserved_quantity if 'reserved_quantity' in quant._fields else 0.0
            qty += (quant.quantity or 0.0) - (reserved or 0.0)

        return max(qty, 0.0)

    def _stone_workshop_get_line_uom(self):
        self.ensure_one()

        for field_name in ('product_uom_id', 'product_uom'):
            if field_name in self._fields:
                uom = self[field_name]
                if uom:
                    return uom

        if self.product_id and self.product_id.uom_id:
            return self.product_id.uom_id

        return self.env['uom.uom']

    def _stone_workshop_needs_supply(self):
        self.ensure_one()

        if not self.stone_workshop_required:
            return False
        if self.stone_workshop_order_id and self.stone_workshop_order_id.state != 'cancel':
            return False
        if not self.stone_workshop_auto_create:
            return False
        if self.display_type or self.stone_is_workshop_service_line:
            return False
        if not self.product_id or self.product_id.type == 'service':
            return False
        if not self.stone_workshop_base_product_id or not self.stone_workshop_process_id:
            return False

        trigger = self.stone_workshop_trigger or 'on_shortage'

        if trigger == 'manual':
            return False
        if trigger == 'always':
            return True

        return self._stone_workshop_shortage_detected()

    def _stone_workshop_shortage_detected(self):
        """True si el producto final vendido NO está cubierto por inventario.

        Cubierto = reservado/hecho en esta línea + libre real del producto
        final en el almacén de la orden.
        """
        self.ensure_one()

        line_uom = self._stone_workshop_get_line_uom()
        rounding = (
            (line_uom.rounding if line_uom else 0.0)
            or (self.product_id.uom_id.rounding if self.product_id and self.product_id.uom_id else 0.0)
            or 0.00001
        )

        required = self.product_uom_qty or 0.0
        reserved_on_sale = self._stone_workshop_line_qty_done_or_reserved()
        free_final = self._stone_workshop_final_available_qty()
        covered = reserved_on_sale + free_final

        return float_compare(covered, required, precision_rounding=rounding) < 0

    def _stone_workshop_production_target_vals(self):
        """Objetivo de producción de la OT según la UoM del producto vendido.

        `production_target_sqm` son m²: solo se llena con la cantidad vendida
        cuando la UoM del producto es de área. Para productos por pieza se
        deja en 0 (el taller usa el área de entrada como objetivo de corte) y
        la cantidad va a `target_pieces`; antes se copiaban las piezas como si
        fueran m², distorsionando estimados de tiempo y validaciones de corte.
        """
        self.ensure_one()
        qty = self.product_uom_qty or 0.0
        Selection = self.env['sale.stone.workshop.input.selection']

        if self.product_id and Selection._product_uom_is_area(self.product_id):
            return {'production_target_sqm': qty, 'target_pieces': 1}

        return {'production_target_sqm': 0.0, 'target_pieces': int(qty) or 1}

    def _stone_workshop_confirm_should_create(self):
        """Decide si al confirmar la venta esta línea debe generar su OT.

        Regla:
        - Si el vendedor ya seleccionó placas base, la intención es explícita:
          SIEMPRE se crea la OT (comportamiento histórico del flujo visual).
        - Sin selección de placas, se respeta la configuración del producto:
          `auto_create` desactivado o disparador Manual NO crean OT, y con
          disparador "Solo si falta inventario final" se verifica el faltante
          (permite surtir del stock de producto final existente).
        """
        self.ensure_one()

        if self._stone_workshop_active_input_selections():
            return True
        if not self.stone_workshop_auto_create:
            return False

        trigger = self.stone_workshop_trigger or 'on_shortage'
        if trigger == 'manual':
            return False
        if trigger == 'always':
            return True

        return self._stone_workshop_shortage_detected()

    # -------------------------------------------------------------------------
    # Selección de placas base para taller desde la venta
    # -------------------------------------------------------------------------

    def _stone_workshop_active_input_selections(self):
        return self.stone_workshop_input_selection_ids.filtered(
            lambda s: s.state != 'cancelled'
        )

    def _stone_workshop_assert_can_select_base_inputs(self):
        self.ensure_one()

        if self.display_type or self.stone_is_workshop_service_line:
            raise UserError(_('Esta línea no permite selección de placas base.'))
        if self.order_id.state == 'cancel':
            raise UserError(_('La orden de venta está cancelada; no puedes seleccionar placas.'))
        if not self.stone_workshop_required:
            raise UserError(_('La línea no está marcada como Requiere taller.'))
        if not self.stone_workshop_base_product_id:
            raise UserError(_('Configura el producto base antes de seleccionar placas.'))
        if not self.stone_workshop_process_id:
            raise UserError(_('Configura el proceso de taller antes de seleccionar placas.'))

    def _stone_workshop_safe_int_list(self, values):
        result = []
        for value in values or []:
            try:
                clean = int(value)
            except (TypeError, ValueError):
                continue
            if clean and clean not in result:
                result.append(clean)
        return result

    def _stone_workshop_parse_qty_breakdown(self, breakdown):
        result = {}
        if not breakdown:
            return result

        if isinstance(breakdown, str):
            try:
                breakdown = json.loads(breakdown)
            except (json.JSONDecodeError, TypeError):
                breakdown = {}

        if not isinstance(breakdown, dict):
            return result

        for key, value in breakdown.items():
            try:
                lot_id = int(key)
                qty = float(value or 0.0)
            except (TypeError, ValueError):
                continue
            if lot_id and qty > 0:
                result[lot_id] = qty

        return result

    def _stone_workshop_prepare_selection_vals_from_lots(self, lot_ids, breakdown=None):
        self.ensure_one()

        safe_lot_ids = self._stone_workshop_safe_int_list(lot_ids)
        if not safe_lot_ids:
            return []

        breakdown = self._stone_workshop_parse_qty_breakdown(breakdown)
        warehouse = self.order_id.warehouse_id
        location_id = warehouse.lot_stock_id.id if warehouse and warehouse.lot_stock_id else False

        vals_list = self.env['workshop.order'].prepare_input_line_vals_from_lots(
            self.stone_workshop_base_product_id.id,
            safe_lot_ids,
            location_id=location_id,
        )

        for vals in vals_list:
            lot_id = vals.get('lot_id')
            if isinstance(lot_id, (list, tuple)):
                lot_id = lot_id[0] if lot_id else False

            try:
                lot_id = int(lot_id or 0)
            except (TypeError, ValueError):
                lot_id = 0

            vals.pop('product_id', None)

            manual_qty = breakdown.get(lot_id)
            if manual_qty and manual_qty > 0:
                vals['qty_in'] = manual_qty
                vals['area_sqm'] = manual_qty

            vals.update({
                'sale_order_id': self.order_id.id,
                'sale_line_id': self.id,
                'product_final_id': self.product_id.id,
                'base_product_id': self.stone_workshop_base_product_id.id,
                'reserved_origin': '%s / %s' % (
                    self.order_id.name or '',
                    self.product_id.display_name or '',
                ),
                'state': 'selected',
            })

        return vals_list

    def _stone_workshop_validate_lots_not_committed_elsewhere(self, target_lot_ids):
        self.ensure_one()

        target_lot_ids = set(target_lot_ids or [])
        if not target_lot_ids:
            return True

        current_lot_ids = set(self._stone_workshop_active_input_selections().mapped('lot_id').ids)

        if self.stone_workshop_order_id:
            current_lot_ids.update(
                self.stone_workshop_order_id.input_line_ids.filtered(
                    lambda l: l.state != 'cancelled' and l.lot_id
                ).mapped('lot_id').ids
            )

        committed_lot_ids = set(
            self.env['stock.quant']._get_committed_lot_ids(
                self.stone_workshop_base_product_id.id
            )
        )

        conflict_ids = target_lot_ids & (committed_lot_ids - current_lot_ids)

        if conflict_ids:
            lots = self.env['stock.lot'].browse(list(conflict_ids))
            raise UserError(_(
                'No puedes seleccionar estas placas porque ya están comprometidas en otra venta, apartado u orden de taller:\n%s'
            ) % ', '.join(lots.mapped('name')))

        return True

    def get_workshop_input_selector_data(self):
        self.ensure_one()

        selections = self._stone_workshop_active_input_selections()
        selected_lot_ids = selections.mapped('lot_id').ids

        breakdown = {
            str(selection.lot_id.id): selection.qty_in
            for selection in selections
            if selection.lot_id and selection.qty_in
        }

        return {
            'line_id': self.id,
            'sale_order_id': self.order_id.id,
            'sale_order_name': self.order_id.name or '',
            'state': self.order_id.state,
            'can_select': bool(
                self.order_id.state != 'cancel'
                and self.stone_workshop_required
                and self.stone_workshop_base_product_id
                and self.stone_workshop_process_id
            ),
            'workshop_required': bool(self.stone_workshop_required),
            'has_workshop_order': bool(self.stone_workshop_order_id),
            'workshop_order_id': self.stone_workshop_order_id.id if self.stone_workshop_order_id else False,
            'workshop_order_name': self.stone_workshop_order_id.name if self.stone_workshop_order_id else '',
            'product_final_id': self.product_id.id if self.product_id else False,
            'product_final_name': self.product_id.display_name if self.product_id else '',
            'base_product_id': self.stone_workshop_base_product_id.id if self.stone_workshop_base_product_id else False,
            'base_product_name': self.stone_workshop_base_product_id.display_name if self.stone_workshop_base_product_id else '',
            'process_id': self.stone_workshop_process_id.id if self.stone_workshop_process_id else False,
            'process_name': self.stone_workshop_process_id.display_name if self.stone_workshop_process_id else '',
            'requested_qty': self.product_uom_qty or 0.0,
            'selected_lot_ids': selected_lot_ids,
            'breakdown': breakdown,
            'selected_count': len(selections),
            'selected_qty': sum(selections.mapped('qty_in')),
            'selections': [{
                'id': selection.id,
                'lot_id': selection.lot_id.id,
                'lot_name': selection.lot_id.name or '',
                'base_product_id': selection.base_product_id.id,
                'base_product_name': selection.base_product_id.display_name or '',
                'qty_in': selection.qty_in or 0.0,
                'area_sqm': selection.area_sqm or 0.0,
                'material_type': selection.material_type or '',
                'state': selection.state or '',
                'state_label': dict(selection._fields['state'].selection).get(selection.state, selection.state),
                'location_id': selection.location_id.id if selection.location_id else False,
                'location_name': selection.location_id.display_name if selection.location_id else '',
                'workshop_order_id': selection.workshop_order_id.id if selection.workshop_order_id else False,
                'workshop_order_name': selection.workshop_order_id.name if selection.workshop_order_id else '',
            } for selection in selections],
        }

    def write_workshop_input_selection_from_lots(self, lot_ids=None, breakdown=None):
        self.ensure_one()
        self._stone_workshop_assert_can_select_base_inputs()

        safe_lot_ids = self._stone_workshop_safe_int_list(lot_ids)
        self._stone_workshop_validate_lots_not_committed_elsewhere(safe_lot_ids)

        Selection = self.env['sale.stone.workshop.input.selection']
        active_selections = self._stone_workshop_active_input_selections()

        locked = active_selections.filtered(
            lambda s: s.workshop_input_line_id and s.workshop_input_line_id.is_consumed
        )
        if locked:
            raise UserError(_(
                'No puedes modificar la selección porque ya hay placas enviadas/consumidas en taller: %s'
            ) % ', '.join(locked.mapped('lot_id.name')))

        vals_list = self._stone_workshop_prepare_selection_vals_from_lots(
            safe_lot_ids,
            breakdown=breakdown,
        )

        vals_by_lot = {}
        for vals in vals_list:
            lot_id = vals.get('lot_id')
            if isinstance(lot_id, (list, tuple)):
                lot_id = lot_id[0] if lot_id else False
            if lot_id:
                vals_by_lot[int(lot_id)] = vals

        to_cancel = active_selections.filtered(lambda s: s.lot_id.id not in safe_lot_ids)
        for selection in to_cancel:
            input_line = selection.workshop_input_line_id
            if input_line and input_line.exists() and not input_line.is_consumed:
                input_line.unlink()

            selection.write({
                'state': 'cancelled',
                'workshop_input_line_id': False,
                'workshop_order_id': False,
            })

        for lot_id in safe_lot_ids:
            vals = vals_by_lot.get(lot_id)
            if not vals:
                continue

            selection = active_selections.filtered(lambda s: s.lot_id.id == lot_id)[:1]
            if selection:
                allowed_vals = dict(vals)
                allowed_vals.pop('sale_order_id', None)
                allowed_vals.pop('sale_line_id', None)
                allowed_vals.pop('product_final_id', None)
                selection.write(allowed_vals)
            else:
                Selection.create(vals)

        if self.stone_workshop_order_id:
            self._stone_workshop_push_input_selections_to_workshop(self.stone_workshop_order_id)

        return self.get_workshop_input_selector_data()

    def _stone_workshop_push_input_selections_to_workshop(self, workshop):
        self.ensure_one()

        if not workshop:
            return False

        active_selections = self._stone_workshop_active_input_selections()
        if not active_selections:
            return False

        if workshop.state in ('in_workshop', 'done', 'cancel'):
            consumed = active_selections.filtered(
                lambda s: s.workshop_input_line_id and s.workshop_input_line_id.is_consumed
            )
            if consumed:
                return True

        WorkshopInput = self.env['workshop.input.line']
        created_or_updated = WorkshopInput

        for selection in active_selections:
            vals = selection._to_workshop_input_vals(workshop)
            input_line = selection.workshop_input_line_id

            if input_line and input_line.exists():
                if input_line.is_consumed:
                    continue
                input_line.with_context(skip_sale_workshop_reservation=True).write(vals)
            else:
                input_line = WorkshopInput.with_context(skip_sale_workshop_reservation=True).create(vals)

            selection.write({
                'workshop_order_id': workshop.id,
                'workshop_input_line_id': input_line.id,
            })
            created_or_updated |= input_line

        if created_or_updated and hasattr(workshop, '_sale_workshop_refresh_input_reservation'):
            workshop._sale_workshop_refresh_input_reservation()

        active_selections._sync_state_from_workshop_input()

        return True

    # -------------------------------------------------------------------------
    # Inventario para selector de producto base de taller
    # -------------------------------------------------------------------------

    def _swis_safe_float(self, value, default=0.0):
        try:
            if value in (False, None, ''):
                return default
            if isinstance(value, str):
                value = value.replace(',', '.')
            return float(value)
        except (TypeError, ValueError):
            return default

    def _swis_get_first_value(self, records, field_names, default=False):
        for record in records:
            if not record or not record.exists():
                continue

            for field_name in field_names:
                if field_name not in record._fields:
                    continue

                value = record[field_name]
                if not value:
                    continue

                field = record._fields[field_name]
                if field.type == 'many2one':
                    return value.display_name if value else default

                return value

        return default

    def _swis_normalize_material_type(self, value):
        value = (value or '').strip().lower()

        if value in ('slab', 'placa', 'plate'):
            return 'placa'
        if value in ('format', 'formato'):
            return 'formato'
        if value in ('piece', 'pieza'):
            return 'pieza'
        if value in ('remnant', 'retazo', 'sobrante'):
            return 'retazo'

        return value or 'placa'

    def _swis_text_match(self, value, expected):
        if not expected:
            return True

        value = (value or '').strip().lower()
        expected = (expected or '').strip().lower()

        return expected in value

    def _swis_own_reserved_qty_for_lot(self, product, lot, location=False):
        self.ensure_one()

        workshop = self.stone_workshop_order_id
        if not workshop or not hasattr(workshop, '_sale_workshop_reserved_qty_for_lot'):
            return 0.0

        return workshop._sale_workshop_reserved_qty_for_lot(
            product,
            lot,
            location=location,
        )

    def search_workshop_input_inventory_for_selector(
        self,
        filters=None,
        current_lot_ids=None,
        page=0,
        page_size=35,
    ):
        """
        Buscador dedicado para placas base de taller.

        No reutiliza search_stone_inventory_for_so_paginated porque ese método
        pertenece a la selección comercial del producto vendido. Aquí se busca
        el producto base configurado en stone_workshop_base_product_id.
        """
        self.ensure_one()

        filters = filters or {}
        current_lot_ids = set(self._stone_workshop_safe_int_list(current_lot_ids or []))
        page = int(page or 0)
        page_size = int(page_size or 35)

        if page < 0:
            page = 0
        if page_size <= 0:
            page_size = 35

        if not self.stone_workshop_required:
            return {
                'items': [],
                'total': 0,
                'page': page,
                'page_size': page_size,
                'message': 'La línea no requiere taller.',
            }

        base_product = self.stone_workshop_base_product_id
        if not base_product:
            return {
                'items': [],
                'total': 0,
                'page': page,
                'page_size': page_size,
                'message': 'La línea no tiene producto base configurado.',
            }

        Quant = self.env['stock.quant'].sudo()

        warehouse = self.order_id.warehouse_id if self.order_id else False
        warehouse_location = warehouse.lot_stock_id if warehouse and warehouse.lot_stock_id else False

        domain = [
            ('product_id', '=', base_product.id),
            ('lot_id', '!=', False),
            ('location_id.usage', '=', 'internal'),
            ('quantity', '>', 0),
        ]

        if warehouse_location:
            domain.append(('location_id', 'child_of', warehouse_location.id))

        lot_name = (filters.get('lot_name') or '').strip()
        if lot_name:
            domain.append(('lot_id.name', 'ilike', lot_name))

        # Las placas con hold/apartado comercial activo están bloqueadas y no
        # pueden seleccionarse como insumo de taller. Es el mismo criterio que el
        # selector de venta (sale_stone_selection._build_stone_domain), que
        # excluye x_tiene_hold; aquí faltaba replicarlo.
        if 'x_tiene_hold' in Quant._fields:
            domain.append(('x_tiene_hold', '=', False))

        current_selection_lot_ids = set(
            self._stone_workshop_active_input_selections().mapped('lot_id').ids
        )

        current_workshop_lot_ids = set()
        if self.stone_workshop_order_id:
            current_workshop_lot_ids = set(
                self.stone_workshop_order_id.input_line_ids.filtered(
                    lambda l: l.state != 'cancelled' and l.lot_id
                ).mapped('lot_id').ids
            )

        allowed_current_lot_ids = current_lot_ids | current_selection_lot_ids | current_workshop_lot_ids

        committed_lot_ids = set(Quant._get_committed_lot_ids(base_product.id))

        # Placas que siguen físicamente en una ubicación interna (in stock) pero
        # ya están vinculadas a una OT de taller en proceso ("en producción / en
        # taller") están bloqueadas mientras se transforman y tampoco deben poder
        # seleccionarse. Se detecta igual que el dashboard visual
        # (inventory_visual_enhanced._iv_get_workshop_lot_ids), pero de forma
        # autocontenida para no depender de ese módulo.
        if 'workshop.input.line' in self.env:
            workshop_blocked_lines = self.env['workshop.input.line'].sudo().search([
                ('product_id', '=', base_product.id),
                ('lot_id', '!=', False),
                ('state', 'not in', ('cancelled', 'done', 'rejected')),
                ('order_id.state', '=', 'in_workshop'),
            ])
            committed_lot_ids |= set(workshop_blocked_lines.mapped('lot_id').ids)

        committed_lot_ids -= allowed_current_lot_ids

        quants = Quant.search(domain, order='lot_id, location_id')
        lot_map = {}

        for quant in quants:
            lot = quant.lot_id

            if not lot:
                continue

            if lot.id in committed_lot_ids:
                continue

            reserved_qty = quant.reserved_quantity if 'reserved_quantity' in quant._fields else 0.0
            free_qty = (quant.quantity or 0.0) - (reserved_qty or 0.0)

            own_reserved_qty = 0.0
            if lot.id in allowed_current_lot_ids:
                own_reserved_qty = self._swis_own_reserved_qty_for_lot(
                    base_product,
                    lot,
                    location=quant.location_id,
                )

            effective_qty = free_qty + own_reserved_qty

            if effective_qty <= 0 and lot.id not in allowed_current_lot_ids:
                continue

            if lot.id not in lot_map:
                tipo = self._swis_get_first_value(
                    [quant, lot],
                    ['x_tipo', 'material_type', 'stone_material_type', 'tipo_material'],
                    default='placa',
                )
                tipo = self._swis_normalize_material_type(tipo)

                alto = self._swis_safe_float(self._swis_get_first_value(
                    [quant, lot],
                    ['x_alto', 'alto', 'height_cm', 'marble_height', 'height'],
                    default=0.0,
                ))
                ancho = self._swis_safe_float(self._swis_get_first_value(
                    [quant, lot],
                    ['x_ancho', 'ancho', 'width_cm', 'marble_width', 'width'],
                    default=0.0,
                ))

                lot_map[lot.id] = {
                    'id': quant.id,
                    'lot_id': [lot.id, lot.name or lot.display_name],
                    'location_id': [
                        quant.location_id.id,
                        quant.location_id.display_name or quant.location_id.name or '',
                    ],
                    'quantity': 0.0,
                    'x_bloque': self._swis_get_first_value(
                        [quant, lot],
                        ['x_bloque', 'bloque', 'block_name', 'lot_general'],
                        default='',
                    ),
                    'x_atado': self._swis_get_first_value(
                        [quant, lot],
                        ['x_atado', 'atado', 'bundle_name'],
                        default='',
                    ),
                    'x_alto': alto,
                    'x_ancho': ancho,
                    'x_tipo': tipo,
                    'x_color': self._swis_get_first_value(
                        [quant, lot],
                        ['x_color', 'color', 'tone', 'tono'],
                        default='',
                    ),
                    'reserved_by_this_workshop': 0.0,
                    'free_qty': 0.0,
                }

            lot_map[lot.id]['quantity'] += max(effective_qty, 0.0)
            lot_map[lot.id]['reserved_by_this_workshop'] += max(own_reserved_qty, 0.0)
            lot_map[lot.id]['free_qty'] += max(free_qty, 0.0)

        items = list(lot_map.values())

        bloque = (filters.get('bloque') or '').strip()
        atado = (filters.get('atado') or '').strip()
        tipo_raw = (filters.get('tipo') or '').strip()
        tipo_filter = self._swis_normalize_material_type(tipo_raw)
        alto_min = self._swis_safe_float(filters.get('alto_min'))
        ancho_min = self._swis_safe_float(filters.get('ancho_min'))

        filtered_items = []

        for item in items:
            if bloque and not self._swis_text_match(item.get('x_bloque'), bloque):
                continue
            if atado and not self._swis_text_match(item.get('x_atado'), atado):
                continue
            if tipo_raw and item.get('x_tipo') != tipo_filter:
                continue
            if alto_min and self._swis_safe_float(item.get('x_alto')) < alto_min:
                continue
            if ancho_min and self._swis_safe_float(item.get('x_ancho')) < ancho_min:
                continue

            filtered_items.append(item)

        filtered_items.sort(key=lambda x: (x.get('lot_id') or [0, ''])[1] or '')

        total = len(filtered_items)
        start = page * page_size
        end = start + page_size
        paged_items = filtered_items[start:end]

        _logger.info(
            '[SWIS INVENTORY] sale_line=%s sale=%s base_product=%s quants=%s total=%s got=%s committed=%s allowed_current=%s',
            self.id,
            self.order_id.name if self.order_id else '',
            base_product.id,
            len(quants),
            total,
            len(paged_items),
            len(committed_lot_ids),
            list(allowed_current_lot_ids),
        )

        return {
            'items': paged_items,
            'total': total,
            'page': page,
            'page_size': page_size,
            'product_id': base_product.id,
            'product_name': base_product.display_name,
        }

    # -------------------------------------------------------------------------
    # Acciones
    # -------------------------------------------------------------------------

    def action_view_stone_workshop_order(self):
        self.ensure_one()

        if not self.stone_workshop_order_id:
            raise UserError(_('Esta línea no tiene orden de taller vinculada.'))

        return {
            'type': 'ir.actions.act_window',
            'name': _('Orden de Taller'),
            'res_model': 'workshop.order',
            'view_mode': 'form',
            'res_id': self.stone_workshop_order_id.id,
            'target': 'current',
        }

    def action_create_stone_workshop_order(self):
        created = self.order_id._stone_workshop_create_missing_orders(force_lines=self)

        if len(created) == 1:
            return {
                'type': 'ir.actions.act_window',
                'name': _('Orden de Taller'),
                'res_model': 'workshop.order',
                'view_mode': 'form',
                'res_id': created.id,
                'target': 'current',
            }

        return True

    def _stone_workshop_parse_breakdown(self):
        self.ensure_one()

        raw = getattr(self, 'x_lot_breakdown_json', False)

        if not raw:
            return {}

        if isinstance(raw, dict):
            return dict(raw)

        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return {}

        return {}