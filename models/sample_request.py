# -*- coding: utf-8 -*-
"""SOLICITUD DE MUESTRAS (27 sep 2026).

Un vendedor pide muestras para un cliente/proyecto. Dos escenarios:

- ``cut``: corte de muestras. Se eligen los lotes a consumir y las medidas
  de las muestras; autorizada, se imprime el PICK TICKET para que Logística
  lleve el material a Taller; Taller la termina y los lotes se CONSUMEN.
- ``delivery``: entrega de placas completas, sin proceso. Autorizada, se
  entrega con REMISIÓN y los lotes se CONSUMEN.

Consumir = salida a la ubicación "Muestras" (tipo pérdida de inventario)
vía stock.scrap, igual que la Baja de Material: el material desaparece del
stock y NO se genera nada (las muestras no se inventarían). Sin precio.

El pick ticket y la remisión son EXACTAMENTE las plantillas de
sale_delivery_wizard: este modelo expone los mismos campos que
sale.delivery.document (y sus líneas) y ``sale_order_id`` apunta a la propia
solicitud, que trae los datos de cabecera que las plantillas leen de la
orden (folio, vendedor, proyecto, cliente, almacén).

Mientras la solicitud está abierta sus lotes cuentan como COMPROMETIDOS
(stock.quant._get_committed_lot_ids): nadie los vende, aparta ni da de baja.
"""
import logging
from collections import defaultdict

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.fields import Command
from odoo.tools.safe_eval import safe_eval

_logger = logging.getLogger(__name__)

SAMPLE_STATES = [
    ('draft', 'Borrador'),
    ('to_approve', 'Por autorizar'),
    ('approved', 'Autorizada'),
    ('in_workshop', 'En taller'),
    ('done', 'Entregada'),
    ('rejected', 'Rechazada'),
    ('cancel', 'Cancelada'),
]
# Estados en los que los lotes quedan comprometidos por la muestra.
SAMPLE_OPEN_STATES = ('to_approve', 'approved', 'in_workshop')

AUTHORIZER_GROUP = 'inventory_shopping_cart.group_price_authorizer'
# stock.group_stock_user y group_workshop_user NO sirven de candado: casi todos
# los vendedores los tienen. Opera la muestra Logística (Usuario de Entregas)
# o el Administrador de taller.
LOGISTICS_GROUP = 'sale_delivery_wizard.group_delivery_user'
WORKSHOP_GROUP = 'stone_workshop.group_workshop_manager'

# Medidas rápidas del asistente (cm).
SIZE_PRESETS = [(10, 10), (15, 15), (20, 20), (30, 30), (30, 60), (40, 40), (60, 60)]

# Mismos bypass que la Baja de Material: la validación de negocio ya se hizo.
CONSUME_CONTEXT = {
    'skip_hold_validation': True,
    'skip_whole_lot': True,
    'skip_whole_lot_removal': True,
    'skip_whole_lot_no_assign': True,
    'skip_duplicate_lot_validation': True,
    'skip_lot_duplicate_check': True,
    'skip_stock_lot_duplicate_check': True,
}


def _group_users(env, xmlid):
    """Usuarios del grupo, incluidos los que lo reciben por implicación
    (Odoo 19: user_ids solo trae los directos)."""
    group = env.ref(xmlid, raise_if_not_found=False)
    Users = env['res.users']
    if not group:
        return Users
    users = Users
    for fname in ('all_user_ids', 'user_ids'):
        if fname in group._fields:
            users |= group[fname]
    return users.filtered(lambda u: u.active and not u.share)


class SomSampleRequest(models.Model):
    _name = 'som.sample.request'
    _description = 'Solicitud de muestras'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char('Folio', default=lambda self: _('Nueva'), readonly=True, copy=False, tracking=True)
    company_id = fields.Many2one('res.company', 'Compañía', required=True, index=True,
                                 default=lambda self: self.env.company)
    state = fields.Selection(SAMPLE_STATES, 'Estatus', default='draft', required=True,
                             tracking=True, index=True, copy=False)
    sample_type = fields.Selection([
        ('cut', 'Proceso en taller'),
        ('delivery', 'Entrega de placas'),
    ], 'Tipo', required=True, default='cut', tracking=True,
        help='Proceso en taller: acabados en orden (matizar, busardear…) y/o corte; '
             'pasa por Taller con pick ticket. Entrega: placas completas al cliente, '
             'sin proceso (remisión).')

    partner_id = fields.Many2one('res.partner', 'Cliente', required=True, tracking=True, index=True)
    x_project_id = fields.Many2one('project.project', 'Proyecto', tracking=True, index=True)
    x_architect_id = fields.Many2one('res.partner', 'Embajador')
    user_id = fields.Many2one('res.users', 'Vendedor', required=True, tracking=True, index=True,
                              default=lambda self: self.env.user)
    date_needed = fields.Date('Fecha requerida')
    reason = fields.Text('Motivo de la muestra', tracking=True)
    delivery_address = fields.Text('Dirección de entrega')
    instructions = fields.Text('Indicaciones para Logística / Taller')
    # Lo que imprime el pick ticket / la remisión en "Nota interna": la RUTA de
    # taller completa (acabados en orden + corte final) y las indicaciones.
    special_instructions = fields.Text('Nota interna', compute='_compute_special_instructions')
    workshop_route = fields.Text('Ruta en taller', compute='_compute_special_instructions')

    line_ids = fields.One2many('som.sample.request.line', 'request_id', 'Material a consumir', copy=True)
    step_ids = fields.One2many('som.sample.request.step', 'request_id', 'Ruta de acabados', copy=True)
    size_ids = fields.One2many('som.sample.request.size', 'request_id', 'Corte final', copy=True)

    # Autorización
    submitted_date = fields.Datetime('Enviada', readonly=True, copy=False)
    decided_by_id = fields.Many2one('res.users', 'Decidió', readonly=True, copy=False)
    decided_date = fields.Datetime('Fecha de decisión', readonly=True, copy=False)
    rejection_reason = fields.Text('Motivo del rechazo', readonly=True, copy=False)

    # Operación
    pick_ticket_printed = fields.Boolean('Pick ticket impreso', readonly=True, copy=False)
    workshop_date = fields.Datetime('Entregada a taller', readonly=True, copy=False)
    done_date = fields.Datetime('Consumida', readonly=True, copy=False)
    done_by_id = fields.Many2one('res.users', 'Cerró', readonly=True, copy=False)
    scrap_ids = fields.Many2many('stock.scrap', string='Movimientos de consumo', readonly=True, copy=False)

    # Campos que leen las plantillas de pick ticket / remisión
    # (sale_delivery_wizard), con los mismos nombres que sale.delivery.document.
    sale_order_id = fields.Many2one('som.sample.request', compute='_compute_sale_order_id',
                                    string='Documento de origen')
    x_partner_mask_name = fields.Char(compute='_compute_x_partner_mask_name')
    warehouse_id = fields.Many2one('stock.warehouse', 'Almacén', compute='_compute_warehouse_id')
    remission_number = fields.Char('Remisión', readonly=True, copy=False)
    delivery_date = fields.Datetime('Fecha de entrega', readonly=True, copy=False)
    picking_id = fields.Many2one('stock.picking', readonly=True, copy=False)
    out_picking_id = fields.Many2one('stock.picking', readonly=True, copy=False)
    vehicle_id = fields.Many2one('fleet.vehicle', 'Vehículo', copy=False)
    vehicle_driver_id = fields.Many2one('res.partner', 'Chofer', copy=False)
    delivered_by = fields.Char('Entregó (nombre)', copy=False)
    delivered_signature_image = fields.Binary('Firma de quien entregó', copy=False, attachment=True)
    signed_by = fields.Char('Recibió (nombre)', copy=False)
    signature_image = fields.Binary('Firma de quien recibió', copy=False, attachment=True)

    # Métricas
    lot_count = fields.Integer('Lotes', compute='_compute_totals', store=True)
    total_qty = fields.Float('Cantidad', compute='_compute_totals', store=True, digits=(16, 2))
    total_m2 = fields.Float('m²', compute='_compute_totals', store=True, digits=(16, 2))
    piece_count = fields.Integer('Piezas de muestra', compute='_compute_totals', store=True)
    product_names = fields.Char('Materiales', compute='_compute_totals', store=True)

    # UI
    can_decide = fields.Boolean(compute='_compute_permissions')
    can_operate = fields.Boolean(compute='_compute_permissions')

    # ------------------------------------------------------------------
    # Cómputos
    # ------------------------------------------------------------------
    def _compute_sale_order_id(self):
        for rec in self:
            rec.sale_order_id = rec

    @api.depends('partner_id')
    def _compute_x_partner_mask_name(self):
        for rec in self:
            partner = rec.partner_id
            rec.x_partner_mask_name = (
                partner.x_mask_name if partner and 'x_mask_name' in partner._fields else False)

    @api.depends('company_id')
    def _compute_warehouse_id(self):
        Warehouse = self.env['stock.warehouse'].sudo()
        for rec in self:
            rec.warehouse_id = Warehouse.search([('company_id', '=', rec.company_id.id)], limit=1)

    @api.depends('sample_type', 'step_ids.sequence', 'step_ids.process_id', 'step_ids.product_id',
                 'step_ids.note', 'size_ids', 'instructions')
    def _compute_special_instructions(self):
        for rec in self:
            route = rec._som_route_text() if rec.sample_type == 'cut' else ''
            rec.workshop_route = route
            parts = []
            if route:
                parts.append(_('RUTA EN TALLER: %s') % route)
            if rec.instructions:
                parts.append(rec.instructions.strip())
            rec.special_instructions = ' · '.join(parts) or False

    def _som_route_text(self):
        """'1) MATIZADO MARMOL → 2) BUSARDEADO MARMOL → 3) Corte: 30×30 cm × 2'."""
        self.ensure_one()
        steps = []
        for step in self.step_ids.sorted(lambda s: (s.sequence, s.id)):
            label = step.process_id.name or ''
            if step.product_id:
                label += ' (%s)' % step.product_id.display_name
            if step.note:
                label += ' — %s' % step.note
            steps.append(label)
        if self.size_ids:
            steps.append(_('Corte: %s') % ', '.join(self.size_ids.mapped('display_name')))
        return ' → '.join('%s) %s' % (i, s) for i, s in enumerate(steps, 1))

    @api.depends('line_ids.qty_selected', 'line_ids.area_m2', 'line_ids.product_id', 'size_ids.qty')
    def _compute_totals(self):
        for rec in self:
            rec.lot_count = len(rec.line_ids.filtered('lot_id'))
            rec.total_qty = sum(rec.line_ids.mapped('qty_selected'))
            rec.total_m2 = sum(rec.line_ids.mapped('area_m2'))
            rec.piece_count = sum(rec.size_ids.mapped('qty'))
            names = []
            for p in rec.line_ids.mapped('product_id'):
                if p.display_name not in names:
                    names.append(p.display_name)
            rec.product_names = ', '.join(names)[:250]

    @api.depends_context('uid')
    def _compute_permissions(self):
        user = self.env.user
        decide = user.has_group(AUTHORIZER_GROUP)
        operate = decide or user.has_group(LOGISTICS_GROUP) or user.has_group(WORKSHOP_GROUP)
        for rec in self:
            rec.can_decide = decide
            rec.can_operate = operate

    # ------------------------------------------------------------------
    # ORM
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('name') or vals['name'] == _('Nueva'):
                company = self.env['res.company'].browse(vals.get('company_id')) or self.env.company
                vals['name'] = self._som_next_sequence('som.sample.request', company) or _('Nueva')
        return super().create(vals_list)

    def _som_next_sequence(self, code, company):
        Seq = self.env['ir.sequence'].sudo()
        name = Seq.with_company(company).next_by_code(code)
        if name:
            return name
        template = Seq.search([('code', '=', code)], order='company_id', limit=1)
        if not template:
            return False
        template.copy({'company_id': company.id, 'number_next': 1,
                       'name': '%s (%s)' % (template.name, company.name)})
        return Seq.with_company(company).next_by_code(code)

    def unlink(self):
        if any(rec.state not in ('draft', 'cancel', 'rejected') for rec in self):
            raise UserError(_('Solo se pueden borrar solicitudes en borrador, rechazadas o canceladas.'))
        return super().unlink()

    # ------------------------------------------------------------------
    # Disponibilidad de lotes
    # ------------------------------------------------------------------
    def _som_free_quants(self, lot):
        """Existencias internas del lote en la compañía de la solicitud."""
        return self.env['stock.quant'].sudo().search([
            ('lot_id', '=', lot.id),
            ('location_id.usage', '=', 'internal'),
            ('quantity', '>', 0),
            ('company_id', '=', (self.company_id or self.env.company).id),
        ])

    def _som_check_lines(self, for_consume=False):
        """Valida que cada lote exista y esté libre. Al consumir, los lotes de
        ESTA solicitud cuentan como propios (no como comprometidos)."""
        self.ensure_one()
        if not self.line_ids:
            raise UserError(_('Agrega al menos un lote a consumir.'))
        Quant = self.env['stock.quant'].sudo()
        committed = {}
        problems = []
        seen = set()
        for line in self.line_ids:
            lot = line.lot_id
            if not lot:
                problems.append(_('Hay una línea sin lote.'))
                continue
            if lot.id in seen:
                problems.append(_('El lote %s está repetido.') % lot.name)
                continue
            seen.add(lot.id)
            quants = self._som_free_quants(lot)
            available = sum(quants.mapped('quantity'))
            if not quants:
                problems.append(_('El lote %s ya no tiene existencias.') % lot.name)
                continue
            if line.qty_selected <= 0:
                problems.append(_('El lote %s no tiene cantidad a consumir.') % lot.name)
                continue
            if line.qty_selected - available > 1e-4:
                problems.append(_('El lote %(lot)s solo tiene %(qty).2f disponibles.') % {
                    'lot': lot.name, 'qty': available})
                continue
            if any((q.reserved_quantity or 0.0) > 0 for q in quants):
                problems.append(_('El lote %s tiene cantidad reservada por otro documento.') % lot.name)
                continue
            if any(getattr(q, 'x_tiene_hold', False) for q in quants):
                problems.append(_('El lote %s tiene un apartado (hold) activo.') % lot.name)
                continue
            pid = lot.product_id.id
            if pid not in committed:
                ids = set(Quant.with_context(som_sample_exclude_id=self.id)._get_committed_lot_ids(pid))
                committed[pid] = ids
            if lot.id in committed[pid]:
                problems.append(_('El lote %s está comprometido en una venta, apartado, '
                                  'orden de taller u otra muestra.') % lot.name)
        if problems:
            raise UserError(_('No se puede continuar con la solicitud %(name)s:\n\n%(p)s') % {
                'name': self.name, 'p': '\n'.join('- %s' % p for p in problems)})
        return True

    # ------------------------------------------------------------------
    # Flujo
    # ------------------------------------------------------------------
    def action_submit(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_('Solo se envían a autorización solicitudes en borrador.'))
            if not (rec.reason or '').strip():
                raise UserError(_('Escribe el motivo de la muestra: es lo que lee el autorizador.'))
            if rec.sample_type == 'cut' and not rec.size_ids and not rec.step_ids:
                raise UserError(_('Indica qué hace Taller: acabados en orden y/o medidas de corte.'))
            rec._som_check_lines()
            rec.write({'state': 'to_approve', 'submitted_date': fields.Datetime.now()})
            rec._som_notify_authorizers()
        return True

    def _som_notify_authorizers(self):
        """Una actividad COMPARTIDA por autorizador (tipo "Autorizar muestra"
        del Centro de Actividades): la primera resolución cierra a los demás."""
        for rec in self:
            authorizers = _group_users(rec.env, AUTHORIZER_GROUP)
            kind = dict(rec._fields['sample_type']._description_selection(rec.env)).get(rec.sample_type)
            note = Markup(
                '<p><b>%s</b> pide %s para <b>%s</b>%s.</p>'
                '<p>%s lote(s) · %.2f m²%s</p>%s<p><b>Motivo:</b> %s</p>') % (
                rec.user_id.name, (kind or '').lower(), rec.partner_id.display_name,
                (' · %s' % rec.x_project_id.name) if rec.x_project_id else '',
                rec.lot_count, rec.total_m2,
                (' · %s pieza(s)' % rec.piece_count) if rec.piece_count else '',
                Markup('<p><b>Ruta en taller:</b> %s</p>') % rec.workshop_route if rec.workshop_route else '',
                rec.reason or '')
            for user in authorizers:
                rec.sudo().activity_schedule(
                    'mail.mail_activity_data_todo',
                    summary=_('Autorizar muestra · %s') % rec.name,
                    note=note, user_id=user.id)
            rec.message_post(body=_('Enviada a autorización (%s autorizador(es)).') % len(authorizers),
                             message_type='notification', subtype_xmlid='mail.mt_note')

    def _som_check_authorizer(self):
        if not self.env.user.has_group(AUTHORIZER_GROUP):
            raise AccessError(_('Solo un autorizador puede aprobar o rechazar muestras.'))

    def action_approve(self):
        self._som_check_authorizer()
        for rec in self:
            if rec.state != 'to_approve':
                raise UserError(_('La solicitud %s ya no está por autorizar.') % rec.name)
            rec._som_check_lines()
            rec.write({'state': 'approved', 'decided_by_id': self.env.user.id,
                       'decided_date': fields.Datetime.now(), 'rejection_reason': False})
            rec._som_close_auth_activities(_('Autorizada por %s') % self.env.user.name)
            rec._som_notify_result(True)
        return True

    def action_reject(self, reason=None):
        self._som_check_authorizer()
        reason = (reason or '').strip()
        if not reason:
            raise UserError(_('Indica el motivo del rechazo.'))
        for rec in self:
            if rec.state != 'to_approve':
                raise UserError(_('La solicitud %s ya no está por autorizar.') % rec.name)
            rec.write({'state': 'rejected', 'decided_by_id': self.env.user.id,
                       'decided_date': fields.Datetime.now(), 'rejection_reason': reason})
            rec._som_close_auth_activities(_('Rechazada por %s: %s') % (self.env.user.name, reason))
            rec._som_notify_result(False)
        return True

    def action_open_reject_wizard(self):
        self.ensure_one()
        self._som_check_authorizer()
        return {
            'type': 'ir.actions.act_window', 'name': _('Rechazar muestra'),
            'res_model': 'som.sample.reject.wizard', 'view_mode': 'form', 'target': 'new',
            'context': {'default_request_id': self.id},
        }

    def _som_close_auth_activities(self, feedback):
        for rec in self:
            acts = rec.sudo().activity_ids.filtered(
                lambda a: (a.summary or '').startswith(_('Autorizar muestra')))
            if acts:
                acts.action_feedback(feedback=feedback)

    def _som_notify_result(self, approved):
        """Aviso al vendedor (y a quien la capturó) en el Centro de Actividades."""
        for rec in self:
            users = (rec.user_id | rec.create_uid).filtered(
                lambda u: u.active and not u.share and u != self.env.user)
            if approved:
                summary = _('Muestra autorizada · %s') % rec.name
                if rec.sample_type == 'cut':
                    body = _('Tu solicitud %s fue autorizada. Logística ya puede imprimir el pick '
                             'ticket y llevar el material a Taller.') % rec.name
                else:
                    body = _('Tu solicitud %s fue autorizada. Logística ya puede entregarla '
                             'con remisión.') % rec.name
            else:
                summary = _('Muestra rechazada · %s') % rec.name
                body = _('Tu solicitud %(name)s fue rechazada: %(r)s') % {
                    'name': rec.name, 'r': rec.rejection_reason or ''}
            for user in users:
                rec.sudo().activity_schedule('mail.mail_activity_data_todo', summary=summary,
                                             note=body, user_id=user.id)
            rec.message_post(body=body, message_type='notification', subtype_xmlid='mail.mt_note')

    def _som_check_operator(self):
        user = self.env.user
        if not (user.has_group(LOGISTICS_GROUP) or user.has_group(WORKSHOP_GROUP)
                or user.has_group(AUTHORIZER_GROUP)):
            raise AccessError(_('Solo Logística o Taller pueden operar la muestra.'))

    def action_print_pick_ticket(self):
        self.ensure_one()
        if self.state not in ('approved', 'in_workshop', 'done'):
            raise UserError(_('El pick ticket se imprime cuando la muestra está autorizada.'))
        if not self.pick_ticket_printed:
            self.sudo().write({'pick_ticket_printed': True})
            self.message_post(body=_('Pick ticket impreso por %s.') % self.env.user.name,
                              message_type='notification', subtype_xmlid='mail.mt_note')
        return self.env.ref('sale_stone_workshop_integration.action_report_sample_pick_ticket').report_action(self)

    def action_send_to_workshop(self):
        """Logística entregó el material a Taller."""
        self._som_check_operator()
        for rec in self:
            if rec.sample_type != 'cut' or rec.state != 'approved':
                raise UserError(_('Solo las muestras de taller autorizadas pasan a Taller.'))
            rec._som_check_lines()
            rec.sudo().write({'state': 'in_workshop', 'workshop_date': fields.Datetime.now()})
            rec.message_post(body=_('Material entregado a Taller por %s.') % self.env.user.name,
                             message_type='notification', subtype_xmlid='mail.mt_note')
        return True

    def action_finish_workshop(self):
        """Taller terminó las muestras: se consumen los lotes."""
        self._som_check_operator()
        for rec in self:
            if rec.sample_type != 'cut' or rec.state != 'in_workshop':
                raise UserError(_('Solo se terminan muestras de taller que están en Taller.'))
            pending = rec.step_ids.filtered(lambda s: not s.done)
            if pending:
                pending.sudo().write({'done': True, 'done_by_id': self.env.user.id,
                                      'done_date': fields.Datetime.now()})
            rec._som_consume()
            rec.message_post(body=_('Muestras terminadas en Taller por %s. Material consumido.')
                             % self.env.user.name,
                             message_type='notification', subtype_xmlid='mail.mt_note')
        return True

    def action_deliver_remission(self):
        """Entrega sin proceso: consume los lotes, asigna remisión y la imprime."""
        self.ensure_one()
        self._som_check_operator()
        if self.sample_type != 'delivery' or self.state != 'approved':
            raise UserError(_('Solo las entregas de placas autorizadas se entregan con remisión.'))
        self._som_consume()
        number = self._som_next_sequence('som.sample.remission', self.company_id)
        self.sudo().write({'remission_number': number, 'delivery_date': fields.Datetime.now()})
        self.message_post(body=_('Entregada con remisión %(r)s por %(u)s. Material consumido.') % {
            'r': number, 'u': self.env.user.name},
            message_type='notification', subtype_xmlid='mail.mt_note')
        return self.action_print_remission()

    def action_print_remission(self):
        self.ensure_one()
        if not self.remission_number:
            raise UserError(_('La muestra aún no tiene remisión.'))
        return self.env.ref('sale_stone_workshop_integration.action_report_sample_remission').report_action(self)

    def action_cancel(self):
        user = self.env.user
        staff = any(user.has_group(g) for g in (AUTHORIZER_GROUP, LOGISTICS_GROUP, WORKSHOP_GROUP))
        for rec in self:
            if rec.state in ('done', 'cancel'):
                raise UserError(_('La solicitud %s ya está cerrada.') % rec.name)
            # Antes de consumir: la cancela quien la pidió, un autorizador,
            # Logística o Taller. Los lotes dejan de estar comprometidos.
            if not staff and user not in (rec.user_id | rec.create_uid):
                raise AccessError(_('Solo quien la pidió, un autorizador, Logística o Taller '
                                    'pueden cancelarla.'))
            rec._som_close_auth_activities(_('Cancelada por %s') % user.name)
            rec.write({'state': 'cancel'})
        return True

    def action_reset_draft(self):
        for rec in self:
            if rec.state not in ('rejected', 'cancel'):
                raise UserError(_('Solo se regresan a borrador solicitudes rechazadas o canceladas.'))
            rec.write({'state': 'draft', 'decided_by_id': False, 'decided_date': False})
        return True

    # ------------------------------------------------------------------
    # Consumo
    # ------------------------------------------------------------------
    def _som_sample_location(self):
        """Ubicación "Muestras" (pérdida de inventario) de la compañía; se crea
        la primera vez. Separada del desecho para medir el consumo."""
        self.ensure_one()
        Location = self.env['stock.location'].sudo()
        loc = Location.search([('usage', '=', 'inventory'), ('name', '=', 'Muestras'),
                               ('company_id', '=', self.company_id.id)], limit=1)
        if loc:
            return loc
        parent = self.env.ref('stock.stock_location_locations_virtual', raise_if_not_found=False)
        return Location.create({
            'name': 'Muestras', 'usage': 'inventory', 'company_id': self.company_id.id,
            'location_id': parent.id if parent else False,
        })

    def _som_consume(self):
        """Saca del stock los lotes de la solicitud (stock.scrap a "Muestras").
        Lote consumido completo = se archiva. No se produce nada."""
        self.ensure_one()
        self._som_check_lines(for_consume=True)
        location = self._som_sample_location()
        Scrap = self.env['stock.scrap'].sudo().with_company(self.company_id).with_context(**CONSUME_CONTEXT)
        scraps = self.env['stock.scrap'].sudo()
        Picking = self.env['stock.picking'].sudo()
        if hasattr(Picking, '_release_cart_internal_reservations'):
            Picking._release_cart_internal_reservations(
                self.line_ids.mapped('lot_id').ids,
                reason=_('Liberado automáticamente: el lote se consume en la muestra %s.') % self.name)
        for line in self.line_ids:
            lot = line.lot_id
            pending = line.qty_selected
            quants = self._som_free_quants(lot).sorted(lambda q: -q.quantity)
            available = sum(quants.mapped('quantity'))
            locations = []
            for quant in quants:
                if pending <= 1e-6:
                    break
                qty = min(quant.quantity, pending)
                scrap = Scrap.create({
                    'product_id': lot.product_id.id,
                    'product_uom_id': lot.product_id.uom_id.id,
                    'lot_id': lot.id,
                    'scrap_qty': qty,
                    'location_id': quant.location_id.id,
                    'scrap_location_id': location.id,
                    'company_id': self.company_id.id,
                    'origin': self.name,
                })
                scrap.with_context(**CONSUME_CONTEXT).action_validate()
                scraps |= scrap
                pending -= qty
                locations.append(quant.location_id.display_name or '')
            consumed = line.qty_selected - max(pending, 0.0)
            line.sudo().write({'qty_done': consumed, 'location_note': ', '.join(filter(None, locations))})
            whole = available - consumed <= 1e-4
            lot.sudo().message_post(body=_('Consumido en la muestra %(name)s para %(partner)s: %(qty).2f %(uom)s.%(tail)s') % {
                'name': self.name, 'partner': self.partner_id.display_name, 'qty': consumed,
                'uom': lot.product_id.uom_id.name or '',
                'tail': _(' El lote queda archivado.') if whole else ''})
            if whole and 'active' in lot._fields:
                lot.sudo().write({'active': False})
        self.sudo().write({
            'state': 'done', 'done_date': fields.Datetime.now(), 'done_by_id': self.env.user.id,
            'scrap_ids': [Command.link(s.id) for s in scraps],
        })
        return scraps

    # ------------------------------------------------------------------
    # Centro de Actividades: tarjeta de autorización (protocolo genérico
    # de theme_list_modern: _som_auth_card / _som_auth_decide).
    # ------------------------------------------------------------------
    def _som_auth_is_pending(self):
        self.ensure_one()
        return self.state == 'to_approve'

    def _som_auth_card(self):
        self.ensure_one()
        kind = dict(self._fields['sample_type']._description_selection(self.env)).get(self.sample_type)
        fields_ = [
            ('Cliente', self.partner_id.display_name), ('Proyecto', self.x_project_id.name or ''),
            ('Vendedor', self.user_id.name), ('Tipo', kind),
            ('Material', '%s lote(s) · %.2f m²' % (self.lot_count, self.total_m2)),
        ]
        if self.sample_type == 'cut' and self.workshop_route:
            fields_.append(('Ruta en taller', self.workshop_route))
        fields_.append(('Motivo', self.reason or ''))
        lines = [[l.product_id.display_name or '', l.lot_id.name or '',
                  '{:,.2f} {}'.format(l.qty_selected, l.product_id.uom_id.name or '')]
                 for l in self.line_ids]
        return {
            'pending': self.state == 'to_approve',
            'fields': fields_,
            'line_cols': ['Producto', 'Lote', 'Cantidad'],
            'lines': lines,
            'total': '',
            'reject_needs_reason': True,
        }

    def _som_auth_decide(self, approve, note=None):
        self.ensure_one()
        if approve:
            if note:
                self.message_post(body=Markup('<p><b>Comentario de %s:</b> %s</p>') % (self.env.user.name, note))
            return self.action_approve()
        return self.action_reject(note)

    @api.model
    def _som_open_action(self):
        """Lista con el filtro que le sirve a cada quien: el vendedor ve sus
        solicitudes; Logística/Taller, lo que hay que atender."""
        action = self.env['ir.actions.act_window']._for_xml_id(
            'sale_stone_workshop_integration.action_som_sample_request')
        user = self.env.user
        raw = action.get('context') or {}
        ctx = dict(safe_eval(raw, {'uid': self.env.uid}) if isinstance(raw, str) else raw)
        authorizer = user.has_group(AUTHORIZER_GROUP)
        operator = user.has_group(LOGISTICS_GROUP) or user.has_group(WORKSHOP_GROUP)
        if operator and not authorizer:
            ctx['search_default_filter_to_operate'] = 1
        elif not authorizer:
            ctx['search_default_filter_mine'] = 1
        action['context'] = ctx
        return action

    # ------------------------------------------------------------------
    # Asistente guiado (client action som_sample_request)
    # ------------------------------------------------------------------
    @api.model
    def sample_prepare(self):
        user = self.env.user
        return {
            'user': {'id': user.id, 'name': user.name},
            'today': fields.Date.context_today(self).isoformat(),
            'presets': [{'l': a, 'h': b} for a, b in SIZE_PRESETS],
            # Catálogo de procesos del Taller (acabados, reprocesos…): la ruta
            # de la muestra habla el mismo idioma que las órdenes de taller.
            'processes': [{'id': p.id, 'name': p.name, 'type': p.process_type}
                          for p in self.env['workshop.process'].search(
                              [('process_type', '!=', 'cut'), ('company_id', 'in', [False, self.env.company.id])])],
            'can_create': self.env['som.sample.request'].has_access('create'),
        }

    @api.model
    def sample_partner_info(self, partner_id):
        partner = self.env['res.partner'].browse(int(partner_id)).exists()
        if not partner:
            return {}
        projects = self.env['project.project'].search(
            ['|', ('partner_id', '=', False), ('partner_id', 'child_of', partner.commercial_partner_id.id)],
            limit=80, order='name')
        address = partner._display_address(without_company=True) if hasattr(partner, '_display_address') \
            else partner.contact_address
        return {
            'address': (address or '').strip(),
            'projects': [{'id': p.id, 'name': p.name} for p in projects
                         if not p.partner_id or p.partner_id.commercial_partner_id == partner.commercial_partner_id],
        }

    @api.model
    def sample_search_lots(self, query='', exclude_ids=None, limit=40):
        """Lotes LIBRES para muestra: existencias internas sin reserva, sin
        hold y no comprometidos (venta, apartado, taller u otra muestra)."""
        query = (query or '').strip()
        exclude_ids = set(exclude_ids or [])
        domain = [
            ('location_id.usage', '=', 'internal'),
            ('quantity', '>', 0),
            ('lot_id', '!=', False),
            ('company_id', '=', self.env.company.id),
            ('reserved_quantity', '<=', 0),
        ]
        if not query and 'x_ancho' in self.env['stock.lot']._fields:
            # Sin búsqueda: solo placas (lotes con medidas), las más recientes.
            domain.append(('lot_id.x_ancho', '>', 0))
        if query:
            domain += ['|', '|', '|',
                       ('lot_id.name', 'ilike', query),
                       ('product_id.name', 'ilike', query),
                       ('product_id.default_code', 'ilike', query),
                       ('lot_id.x_bloque', 'ilike', query)] \
                if 'x_bloque' in self.env['stock.lot']._fields else \
                ['|', '|', ('lot_id.name', 'ilike', query), ('product_id.name', 'ilike', query),
                 ('product_id.default_code', 'ilike', query)]
        Quant = self.env['stock.quant'].sudo()
        quants = Quant.search(domain, limit=400, order='in_date desc, id desc' if not query else 'product_id, lot_id')
        by_lot = defaultdict(lambda: self.env['stock.quant'].sudo())
        for q in quants:
            if getattr(q, 'x_tiene_hold', False):
                by_lot[q.lot_id.id] = None
            elif by_lot.get(q.lot_id.id, True) is not None:
                by_lot[q.lot_id.id] |= q
        committed = {}
        out = []
        for lot_id, qs in by_lot.items():
            if qs is None or lot_id in exclude_ids or not qs:
                continue
            lot = qs[0].lot_id
            pid = lot.product_id.id
            if pid not in committed:
                committed[pid] = set(Quant._get_committed_lot_ids(pid))
            if lot.id in committed[pid]:
                continue
            qty = sum(qs.mapped('quantity'))
            loc = qs.sorted(lambda q: -q.quantity)[0].location_id
            out.append(self._som_lot_payload(lot, qty, loc))
            if len(out) >= limit:
                break
        return out

    @api.model
    def _som_lot_payload(self, lot, qty, location):
        width = getattr(lot, 'x_ancho', 0.0) or 0.0
        height = getattr(lot, 'x_alto', 0.0) or 0.0
        uom = lot.product_id.uom_id.name or ''
        return {
            'lot_id': lot.id, 'lot': lot.name,
            'product_id': lot.product_id.id, 'product': lot.product_id.display_name,
            'block': getattr(lot, 'x_bloque', '') or '',
            'width': width, 'height': height,
            'qty': qty, 'uom': uom, 'is_m2': SomSampleRequestLine._uom_is_m2(uom),
            'location': (location.display_name or '').split('/')[-1] if location else '',
        }

    @api.model
    def sample_submit(self, payload):
        """Crea la solicitud y la envía a autorización. Devuelve
        {'errors': {...}} o {'id', 'name'}."""
        payload = payload or {}
        errors = {}
        partner = self.env['res.partner'].browse(int(payload.get('partner_id') or 0)).exists()
        if not partner:
            errors['partner_id'] = _('Elige el cliente.')
        stype = payload.get('sample_type')
        if stype not in ('cut', 'delivery'):
            errors['sample_type'] = _('Elige el tipo de muestra.')
        lines = payload.get('lines') or []
        if not lines:
            errors['lines'] = _('Agrega al menos un lote.')
        sizes = payload.get('sizes') or []
        steps = [s for s in (payload.get('steps') or []) if int(s.get('process_id') or 0)]
        if stype == 'cut' and not sizes and not steps:
            errors['sizes'] = _('Indica qué hace Taller: agrega acabados en orden y/o medidas de corte.')
        for s in sizes:
            if float(s.get('length') or 0) <= 0 or float(s.get('height') or 0) <= 0 or int(s.get('qty') or 0) <= 0:
                errors['sizes'] = _('Cada medida necesita largo, alto y número de piezas.')
                break
        if not (payload.get('reason') or '').strip():
            errors['reason'] = _('Escribe el motivo de la muestra.')
        if errors:
            return {'errors': errors}
        vals = {
            'partner_id': partner.id,
            'x_project_id': int(payload.get('project_id') or 0) or False,
            'sample_type': stype,
            'date_needed': payload.get('date_needed') or False,
            'reason': payload.get('reason').strip(),
            'delivery_address': (payload.get('delivery_address') or '').strip() or False,
            'instructions': (payload.get('instructions') or '').strip() or False,
            'line_ids': [Command.create({
                'lot_id': int(l['lot_id']),
                'qty_selected': float(l.get('qty') or 0.0),
            }) for l in lines],
            'size_ids': [Command.create({
                'length_cm': float(s['length']), 'height_cm': float(s['height']),
                'qty': int(s['qty']),
                'product_id': int(s.get('product_id') or 0) or False,
                'note': (s.get('note') or '').strip() or False,
            }) for s in sizes] if stype == 'cut' else [],
            'step_ids': [Command.create({
                'sequence': (i + 1) * 10,
                'process_id': int(s['process_id']),
                'product_id': int(s.get('product_id') or 0) or False,
                'note': (s.get('note') or '').strip() or False,
            }) for i, s in enumerate(steps)] if stype == 'cut' else [],
        }
        try:
            with self.env.cr.savepoint():
                rec = self.create(vals)
                rec.action_submit()
        except (UserError, AccessError) as exc:
            return {'errors': {'general': str(exc.args[0] if exc.args else exc)}}
        return {'id': rec.id, 'name': rec.name}


class SomSampleRequestLine(models.Model):
    _name = 'som.sample.request.line'
    _description = 'Lote a consumir en muestra'
    _order = 'request_id, id'

    request_id = fields.Many2one('som.sample.request', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='request_id.company_id', store=True, index=True)
    state = fields.Selection(related='request_id.state', store=True)
    sample_type = fields.Selection(related='request_id.sample_type', store=True)
    partner_id = fields.Many2one(related='request_id.partner_id', store=True, string='Cliente')
    user_id = fields.Many2one(related='request_id.user_id', store=True, string='Vendedor')
    x_project_id = fields.Many2one(related='request_id.x_project_id', store=True, string='Proyecto')

    lot_id = fields.Many2one('stock.lot', 'Lote', required=True, index=True,
                             context={'active_test': False})
    product_id = fields.Many2one('product.product', 'Producto', related='lot_id.product_id',
                                 store=True, index=True)
    uom_name = fields.Char('Unidad', compute='_compute_dims')
    qty_selected = fields.Float('Cantidad', digits=(16, 4), required=True)
    qty_done = fields.Float('Consumido', digits=(16, 4), readonly=True, copy=False)
    area_m2 = fields.Float('m²', compute='_compute_area', store=True, digits=(16, 2))
    width_cm = fields.Float('Largo', compute='_compute_dims')
    height_cm = fields.Float('Alto', compute='_compute_dims')
    source_location_id = fields.Many2one('stock.location', 'Ubicación', compute='_compute_source_location')
    location_note = fields.Char('Consumido desde', readonly=True, copy=False)
    # Las plantillas lo consultan (máscara comercial de la venta): siempre vacío.
    sale_line_id = fields.Many2one('sale.order.line', readonly=True, copy=False)

    @staticmethod
    def _uom_is_m2(name):
        name = (name or '').lower()
        return 'm²' in name or 'm2' in name or 'cuadr' in name

    @api.depends('lot_id')
    def _compute_dims(self):
        for line in self:
            lot = line.lot_id
            line.width_cm = getattr(lot, 'x_ancho', 0.0) or 0.0
            line.height_cm = getattr(lot, 'x_alto', 0.0) or 0.0
            line.uom_name = lot.product_id.uom_id.name or ''

    @api.depends('qty_selected', 'lot_id')
    def _compute_area(self):
        for line in self:
            uom = line.lot_id.product_id.uom_id.name or ''
            if self._uom_is_m2(uom):
                line.area_m2 = line.qty_selected
            else:
                w = getattr(line.lot_id, 'x_ancho', 0.0) or 0.0
                h = getattr(line.lot_id, 'x_alto', 0.0) or 0.0
                line.area_m2 = (w * h / 10000.0) if (w and h) else 0.0

    @api.depends('lot_id')
    def _compute_source_location(self):
        for line in self:
            quants = line.request_id._som_free_quants(line.lot_id) if line.lot_id and line.request_id else False
            line.source_location_id = quants.sorted(lambda q: -q.quantity)[:1].location_id if quants else False

    @api.onchange('lot_id')
    def _onchange_lot_id(self):
        for line in self:
            if line.lot_id and not line.qty_selected and line.request_id:
                line.qty_selected = sum(line.request_id._som_free_quants(line.lot_id).mapped('quantity'))

    def _format_short_location(self):
        """Mismo recorte que las líneas de sale.delivery.document (pick ticket)."""
        self.ensure_one()
        if not self.source_location_id:
            return self.location_note or '-'
        raw = self.source_location_id.display_name or self.source_location_id.name or ''
        parts = [p.strip() for p in raw.split('/') if p.strip()]
        for anchor in ('existencias', 'stock', 'inventario'):
            idx = next((i for i, p in enumerate(parts) if p.lower() == anchor), None)
            if idx is not None:
                rest = parts[idx + 1:]
                return '/'.join(rest) if rest else parts[idx]
        return parts[-1] if parts else '-'


class SomSampleRequestStep(models.Model):
    """Paso de la ruta en taller, EN ORDEN: p. ej. Taj Mahal pulido que se
    pide mate busardeado → 1) Matizado 2) Busardeado; el corte final son las
    medidas (som.sample.request.size). Taller marca cada paso al hacerlo."""
    _name = 'som.sample.request.step'
    _description = 'Paso de taller de la muestra'
    _order = 'request_id, sequence, id'

    request_id = fields.Many2one('som.sample.request', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='request_id.company_id', store=True)
    sequence = fields.Integer('Orden', default=10)
    process_id = fields.Many2one('workshop.process', 'Proceso', required=True,
                                 domain="[('process_type', '!=', 'cut')]")
    product_id = fields.Many2one('product.product', 'Material',
                                 help='Vacío = a todos los materiales de la solicitud.')
    note = fields.Char('Indicación')
    done = fields.Boolean('Hecho', copy=False)
    done_by_id = fields.Many2one('res.users', 'Hizo', readonly=True, copy=False)
    done_date = fields.Datetime('Fecha', readonly=True, copy=False)

    def write(self, vals):
        if 'done' in vals:
            self.mapped('request_id')._som_check_operator()
            if vals['done']:
                vals.setdefault('done_by_id', self.env.user.id)
                vals.setdefault('done_date', fields.Datetime.now())
            else:
                vals.update({'done_by_id': False, 'done_date': False})
        res = super().write(vals)
        if 'done' in vals:
            for step in self:
                step.request_id.message_post(
                    body=_('%(state)s: %(p)s (%(u)s).') % {
                        'state': _('Paso hecho') if step.done else _('Paso reabierto'),
                        'p': step.process_id.name, 'u': self.env.user.name},
                    message_type='notification', subtype_xmlid='mail.mt_note')
        return res


class SomSampleRequestSize(models.Model):
    _name = 'som.sample.request.size'
    _description = 'Medida de muestra a cortar'
    _order = 'request_id, id'

    request_id = fields.Many2one('som.sample.request', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='request_id.company_id', store=True)
    product_id = fields.Many2one('product.product', 'Material',
                                 help='Vacío = de todos los materiales de la solicitud.')
    length_cm = fields.Float('Largo (cm)', required=True)
    height_cm = fields.Float('Alto (cm)', required=True)
    qty = fields.Integer('Piezas', required=True, default=1)
    note = fields.Char('Nota')

    @api.depends('length_cm', 'height_cm', 'qty', 'product_id')
    def _compute_display_name(self):
        for rec in self:
            base = '%g×%g cm × %s' % (rec.length_cm, rec.height_cm, rec.qty)
            rec.display_name = '%s (%s)' % (base, rec.product_id.display_name) if rec.product_id else base


class SomSampleRejectWizard(models.TransientModel):
    _name = 'som.sample.reject.wizard'
    _description = 'Rechazar solicitud de muestras'

    request_id = fields.Many2one('som.sample.request', required=True)
    reason = fields.Text('Motivo del rechazo')

    def action_confirm(self):
        self.ensure_one()
        self.request_id.action_reject(self.reason)
        return {'type': 'ir.actions.act_window_close'}
