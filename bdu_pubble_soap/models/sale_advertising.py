# -*- encoding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2004-2017 Magnus (<http://www.magnus.nl>). All Rights Reserved
#    $Id$
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################

from odoo import api, fields, models, _
import odoo.addons.decimal_precision as dp
from odoo.exceptions import UserError
from suds.client import Client
from odoo.addons.queue_job.job import job, related_action
from odoo.addons.queue_job.exception import FailedJobError
import datetime


class SaleOrder(models.Model):
    _inherit = ["sale.order"]

    state = fields.Selection([
        ('draft', 'Quotation'),
        ('submitted', 'Submitted for Approval'),
        ('approved1', 'Approved by Sales Mgr'),
        ('pubble', 'Sent to Pubble'),
        ('sent', 'Quotation Sent'),
        ('sale', 'Sale Order'),
        ('done', 'Done'),
        ('cancel', 'Cancelled'),
    ], string='Status', readonly=True, copy=False, index=True, track_visibility='onchange', default='draft')
    date_sent_pubble = fields.Date('Date order sent to Pubble', index=True,
                                    help="Date on which sales order is sent to Pubble.")
    publog_id = fields.Many2one('sofrom.odooto.pubble')


    @job
    def action_pubble(self):
        self.ensure_one()
        res = self.transfer_order_to_pubble()
        if not res:
            return True
        self._cr.commit()
        res.call_wsdl()
        self.date_sent_pubble = fields.Date.context_today(self)
        self.publog_id = res.id
        return True

    @api.multi
    def action_confirm(self):
        for order in self.filtered("advertising"):
            order.action_pubble()
#            order.with_delay().action_pubble()
        super(SaleOrder, self).action_confirm()
        return True

    @api.multi
    def send_pubble(self):
        so_to_pub = self.env['sofrom.odooto.pubble'].search([('id','=', self.publog_id.id)])
        res = so_to_pub.send_pubble()
        return res

    @api.multi
    def transfer_order_to_pubble(self):
        # import pdb; pdb.set_trace()
        self.ensure_one()
        vals = {
                'transmission_id': self.env['sofrom.odooto.pubble'].get_next_ref(),
                'salesorder_extorderid': self.name,
                'salesorder_reference': self.opportunity_subject,
                'salesorder_createdby': self.user_id.name,
                'salesorder_debtor_extaccountingid': self.published_customer.ref,
                'salesorder_debtor_extdebtorid': self.published_customer.ref,
                'salesorder_debtor_addedby': self.published_customer.create_uid.name,
                'salesorder_debtor_addeddate': self.published_customer.create_date,
                'salesorder_debtor_city' : self.published_customer.city,
                'salesorder_debtor_emailadres' : self.published_customer.email,
                'salesorder_debtor_lastmodified' : self.published_customer.write_date,
                'salesorder_debtor_name' : self.published_customer.name,
                'salesorder_debtor_postalcode' : self.published_customer.zip,
                'salesorder_agency' : self.advertising_agency,
                'salesorder_agency_extaccountingid' : self.advertising_agency.ref,
                'salesorder_agency_extdebtorid' : self.advertising_agency.ref,
                'salesorder_agency_addeddate' : self.advertising_agency.create_date,
                'salesorder_agency_city' : self.advertising_agency.city,
                'salesorder_agency_emailadres' : self.advertising_agency.email,
                'salesorder_agency_lastmodified' : self.advertising_agency.write_date,
                'salesorder_agency_name' : self.advertising_agency.name,
                'salesorder_agency_postalcode' : self.advertising_agency.zip
        }
        res = self.env['sofrom.odooto.pubble'].sudo().create(vals)
        counter = 0
        for line in self.order_line:
            if [('line.product_id.product_tmpl_id.categ_id','child_of', self.env.ref('sale_advertising_order.newspaper_advertising_category'))]:
                counter += 1
                lvals = {
                        'order_id': res.id,
                        'ad_adsize_adtypename': line.ad_class.name,
                        'ad_adsize_extadsizeid': line.product_id.default_code,
                        'ad_adsize_height': line.product_id.height,
                        'ad_adsize_name': line.product_id.name,
                        'ad_adsize_width': line.product_id.width,
                        'ad_edition_editiondate': line.adv_issue.issue_date,
                        'ad_edition_extpublicationid': line.title.name,
                        'ad_extplacementid': line.id,
                        'ad_price': 0,
                        'ad_productiondetail_color': True,
                        'ad_productiondetail_isclassified': False,
                        'ad_productiondetail_dtpcomments': line.layout_remark,
                        'ad_productiondetail_placementcomments': line.name,
                        'ad_status': True,
                }
                self.env['soline.from.odooto.pubble'].sudo().create(lvals)
        if counter == 0:
            res = False
        return res

class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    @api.multi
    def unlink(self):
        if self.advertising:
            for order_line in self:
                if order_line.order_id.state in ('sale', 'pubble', 'done'):
                    self.env['sale.order'].search([('id','=', order_line.order_id.id)]).action_pubble()
                    self.env['soline.from.odooto.pubble'].search([('ad_extplacementid','=', order_line.id),
                                                                  ('order_id','=', order_line.order_id.publog_id.id)]).write({'ad_status': False})
        return super(SaleOrderLine, self).unlink()

class SofromOdootoPubble(models.Model):
    _name = 'sofrom.odooto.pubble'

#    @api.depends('transmission_id')
#    def send_pubble(self):
#        so_to_pub = self.env['sofrom.odooto.pubble'].search([('id', '=', self.publog_id.id)])
#        res = self.call_wsdl()
#        return res

    @api.multi
    def get_next_ref(self, vals=None):
        return self.env['ir.sequence'].next_by_code('pubble.itf')

    transmission_id = fields.Char(string='Transmission ID', store=True, size=16, readonly=True)
    pubble_so_line = fields.One2many('soline.from.odooto.pubble', 'order_id', string='Order Lines', copy=True)
    pubble_response = fields.Text('Pubble Response')
    salesorder_extorderid = fields.Char(string='Sale Order Name')
    salesorder_reference = fields.Char(string='Opportunity Subject', size=64)
    salesorder_createdby = fields.Char(string='User Name', size=32)
    salesorder_debtor_extaccountingid = fields.Char(string='Advertiser Number')
    salesorder_debtor_extdebtorid = fields.Char(string='Advertiser Number')
    salesorder_debtor_addedby = fields.Char(string='Advertiser Added by', size=32)
    salesorder_debtor_addeddate = fields.Datetime(string='Advertiser Date Added')
    salesorder_debtor_city = fields.Char(string='Advertiser City', size=64)
    salesorder_debtor_emailadres = fields.Char(string='Advertiser Email', size=64)
    salesorder_debtor_lastmodified = fields.Datetime(string='Advertiser Date Last Modified')
    salesorder_debtor_name = fields.Char(string='Advertiser Name', size=64)
    salesorder_debtor_postalcode = fields.Char(string='Advertiser Zip Code', size=32)
    salesorder_agency = fields.Boolean(string='Agency')
    salesorder_agency_extaccountingid = fields.Char(string='Agency Number')
    salesorder_agency_extdebtorid = fields.Char(string='Agency Number')
    salesorder_agency_addeddate = fields.Datetime(string='Agency Date Added')
    salesorder_agency_city = fields.Char(string='Agency City', size=64)
    salesorder_agency_emailadres = fields.Char(string='Agency Email', size=64)
    salesorder_agency_lastmodified = fields.Datetime(string='Agency Date Last Modified')
    salesorder_agency_name = fields.Char(string='Agency Name', size=64)
    salesorder_agency_postalcode = fields.Char(string='Agency Zip Code', size=32)


    def call_wsdl(self):
        self.ensure_one()
        if self.pubble_response:
            raise UserError(_('This Sale Order already has been succesfully sent to Pubble.'))
        transmissionID = int(float(self.transmission_id))
        client = Client("https://ws.pubble.nl/Sales.svc?singleWsdl")
        SalesOrder = client.factory.create('ns1:salesOrder')
        publisher = "testbdudata"
        apiKey = "9tituo3t2qo4zk7emvlb"


        SalesOrder.extOrderID = int(float(self.salesorder_extorderid))
        SalesOrder.reference = self.salesorder_reference
        SalesOrder.createdBy = self.salesorder_createdby
        SalesOrder.debtor.extAccountingID = int(float(self.salesorder_debtor_extaccountingid))
        SalesOrder.debtor.extDebtorID = int(float(self.salesorder_debtor_extdebtorid))
        SalesOrder.debtor.addedBy = self.salesorder_debtor_addedby
        SalesOrder.debtor.addedDate = datetime.datetime.strptime(self.salesorder_debtor_addeddate,'%Y-%m-%d %H:%M:%S').strftime('%Y-%m-%dT%H:%M:%S.%f')
        SalesOrder.debtor.city = self.salesorder_debtor_city
        SalesOrder.debtor.emailadres = self.salesorder_debtor_emailadres
        SalesOrder.debtor.lastModified = datetime.datetime.strptime(self.salesorder_debtor_lastmodified,'%Y-%m-%d %H:%M:%S').strftime('%Y-%m-%dT%H:%M:%S.%f')
        SalesOrder.debtor.name = self.salesorder_debtor_name
        SalesOrder.debtor.postalcode = self.salesorder_debtor_postalcode
        if not self.salesorder_agency:
            SalesOrder.agency = None
        else:
            SalesOrder.agency.extAccountingID = int(float(self.salesorder_agency_extaccountingid))
            SalesOrder.agency.extDebtorID = int(float(self.salesorder_agency_extdebtorid))
            SalesOrder.agency.addedDate = datetime.datetime.strptime(self.salesorder_agency_addeddate,'%Y-%m-%d %H:%M:%S').strftime('%Y-%m-%dT%H:%M:%S.%f')
            SalesOrder.agency.city = self.salesorder_agency_city
            SalesOrder.agency.emailadres = self.salesorder_agency_emailadres
            SalesOrder.agency.lastModified = datetime.datetime.strptime(self.salesorder_agency_lastmodified,'%Y-%m-%d %H:%M:%S').strftime('%Y-%m-%dT%H:%M:%S.%f')
            SalesOrder.agency.name = self.salesorder_agency_name
            SalesOrder.agency.postalcode = self.salesorder_agency_postalcode

        for line in self.pubble_so_line:
            ad = client.factory.create('ns1:adPlacement')
            ad.adSize.adTypeName = line.ad_adsize_adtypename
            ad.adSize.extAdSizeID = int(float(line.ad_adsize_extadsizeid))
            ad.adSize.height = int(float(line.ad_adsize_height))
            ad.adSize.name = line.ad_adsize_name
            ad.adSize.width = int(float(line.ad_adsize_width))
            ad.edition.editionDate = datetime.datetime.strptime(line.ad_edition_editiondate, '%Y-%m-%d').strftime('%Y-%m-%dT%H:%M:%S.%f')
            ad.edition.extPublicationID = line.ad_edition_extpublicationid
            ad.extPlacementID = int(float(line.ad_extplacementid))
            ad.price = 0
            ad.productionDetail.color = "true" if line.ad_productiondetail_color else "false"
            ad.productionDetail.isClassified = "true" if line.ad_productiondetail_isclassified else "false"
            ad.productionDetail.dtpComments = line.ad_productiondetail_dtpcomments
            ad.productionDetail.placementComments = line.ad_productiondetail_placementcomments
            ad.status = "active" if line.ad_status else "deleted"

            SalesOrder.orderLine_Ads.adPlacement.append(ad)

        response = client.service.processOrder(SalesOrder, transmissionID, publisher, apiKey)

        return self.write({'pubble_response': response})


class SoLinefromOdootoPubble(models.Model):
    _name = 'soline.from.odooto.pubble'

    order_id = fields.Many2one('sofrom.odooto.pubble', string='Order Reference', required=True, ondelete='cascade', index=True, copy=False)
    ad_adsize_adtypename = fields.Char(string='Advertising Class Name', size=64)
    ad_adsize_extadsizeid = fields.Char(string='Product ID')
    ad_adsize_height = fields.Integer(string='Height mm')
    ad_adsize_name = fields.Char(string='Advertising Product Name', size=64)
    ad_adsize_width = fields.Integer(string='Width mm')
    ad_edition_editiondate = fields.Date(string='Issue Date')
    ad_edition_extpublicationid = fields.Char(string='Advertising Title Name', size=64)
    ad_extplacementid = fields.Integer(string='Line ID')
    ad_price = fields.Integer(string='Price', default=0)
    ad_productiondetail_color = fields.Boolean(string='Color')
    ad_productiondetail_isclassified = fields.Boolean(string='Classified')
    ad_productiondetail_dtpcomments = fields.Text(string='Layout Remarks')
    ad_productiondetail_placementcomments = fields.Text(string='Placement Remarks')
    ad_status = fields.Boolean(string='Active')


