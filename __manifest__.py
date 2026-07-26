# -*- coding: utf-8 -*-
{
    'name': 'Sale Stone Workshop Integration',
    'version': '19.0.3.7.1',
    'category': 'Sales/Manufacturing',
    'summary': 'Integra venta, selección de placas, taller y entregas para transformar producto base en producto final',
    'description': """
            Integración operativa para piedra natural:
            - Venta siempre sobre el producto final prometido al cliente.
            - Orden de taller vinculada a la línea de venta cuando el producto final requiere proceso.
            - Selección visual independiente de placas base/insumos desde la venta.
            - Reserva de placas base para taller sin asignarlas como lotes de la línea final.
            - Consumo de producto base y producción de producto final desde Stone Workshop.
            - Asignación automática de lotes finales al pedido de origen al recibir salidas de taller.
            - Exclusión de placas reservadas en taller y selecciones base desde el selector visual de venta.
                   """,
    'author': 'Alphaqueb Consulting SAS',
    'website': 'https://alphaqueb.com',
    'license': 'LGPL-3',
    'depends': [
        'sale_management',
        'sale_stock',
        'stock',
        'product',
        'sale_stone_selection',
        'stone_workshop',
        'sale_delivery_wizard',
    ],
    'data': [
        'security/ir.model.access.csv',
        'views/product_views.xml',
        'views/sale_order_views.xml',
        'views/workshop_order_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'sale_stone_workshop_integration/static/src/components/workshop_input_selector/workshop_input_selector.xml',
            'sale_stone_workshop_integration/static/src/components/workshop_input_selector/workshop_input_selector.js',
            'sale_stone_workshop_integration/static/src/scss/workshop_input_selector.scss',
            'sale_stone_workshop_integration/static/src/scss/workshop_chain.scss',
        ],
    },
    'installable': True,
    'application': False,
}