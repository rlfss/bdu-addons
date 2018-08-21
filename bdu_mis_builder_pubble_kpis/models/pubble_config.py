# -*- coding: utf-8 -*-

import datetime, httplib, json, logging, pdb
from odoo import models, fields, api

_logger = logging.getLogger(__name__)

class KpiConfig(models.Model):
    _name        = 'pubble.config'
    _description = 'Connection info to KPI sources'
    server  	 = fields.Char(string='Server',help="servername, without protocol, e.g. ws.pubble.nl" )
    method		 = fields.Char(string='Method and query',help="method with start slash, e.g. /dir/api?date=20180101")
    
    latest_issue   = fields.Date(string='Latest issue',   help="Latest issue processed by Pubble connector")
    latest_run     = fields.Char(string='latest_run',     help="Date of latest run of Pubble connector")
    latest_success = fields.Char(string='Latest success', help="Youngest date when Pubble connector was successfully retrieving info")
    latest_status  = fields.Char(string='Latest status',  help="Status of latest run")
    latest_reason  = fields.Char(string='Latest reason',  help="Reason of status code of latest run")
    
    begin		   = fields.Date(string='Begin', help="begindatum van datumrange in de vorm yyyy-mm-dd")
    end 		   = fields.Date(string='End',  help="einddatum van datumrange in de vorm yyyy-mm-dd", widget="date")
    
    #show only first record to configure, no options to create an additional one
    @api.multi
    def default_view(self):
        configurations = self.search([])
        if not configurations :
            server = "bdu.nl"
            self.write({'server' : server})
            configuration = self.id
        else :
            configuration = configurations[0].id
        action = {
                    "type":"ir.actions.act_window",
                    "res_model":"pubble.config",
                    "view_type":"form",
                    "view_mode":"form",
                    "res_id":configuration,
                    "target":"inline",
        }
        return action

    @api.multi
    def save_config(self):
        self.write({}) #{"server": server, "method":method, "query" : query})
        return True

    def conditional_ad(self, boolean_condition, value_to_ad):
        if boolean_condition :
            return value_to_ad
        else :
            return 0

    def ms_datetime_to_python_date(self, ms_datetime_string):
        #format received is  /Date(1519945200000+0100)/
        startpos   = ms_datetime_string.find("/Date(") + 6
        endpos     = ms_datetime_string.find("+")
        eastern_hemisphere = True
        if endpos==-1:
            endpos = ms_datetime_string.find("-")
            eastern_hemisphere = False
        seconds    = long(ms_datetime_string[startpos:endpos-3])
        tz_secs    = int(ms_datetime_string[endpos+1:endpos+3])*3600 + int(ms_datetime_string[endpos+3:endpos+5])*60
        if eastern_hemisphere :
            local_secs = seconds+tz_secs
        else :
            local_secs = seconds-tz_secs
        date       = datetime.date.fromtimestamp(local_secs)
        return date


    @api.multi
    def automated_do_collect(self):
        configuration = self[0]
        configuration.begin = datetime.date.today()
        configuration.end   = datetime.date.today()
        configuration.write()
        return self.do_collect()
    
    @api.multi 
    def do_collect(self):

        #collect data based on config 	    
        config = self[0] 
        li=config.latest_issue.split('-')
        most_recent = datetime.date(int(li[0]),int(li[1]),int(li[2]))
        if not most_recent :
            most_recent=datetime.date(1970,1,1)
        conn   = httplib.HTTPSConnection(config.server.strip())
        api    = config.method.strip()
        api    = api.replace("$begin",config.begin).replace("$end",config.end)

        conn.request("GET", api)
        response = conn.getresponse()
        answer   = response.read()
        status   = response.status
        reason   = response.reason
        conn.close()	
        
        if (reason == "OK") :

            #lookup values and other init
            title_accounts = self.env['sale.advertising.issue'].search([('parent_id','=', False)])
            pubble_kpis = self.env['mis.pubble.kpi']

            json_anwser = json.loads(answer)
            d = {}
            message = ""
            
            #flatten json and write every page to new or existing records
            for title_summary in json_anwser :
                d['title']      = title_summary['titel']
                _logger.debug("title : " + d['title'])

                #keep track of latest updated issue
                issue_date = self.ms_datetime_to_python_date(title_summary['datum'])
                d['issue_date'] = issue_date
                if issue_date>most_recent :
                    most_recent = issue_date
                
                #analytic accounnt and company via sale.advertising.issue
                title_account   = title_accounts.search([('default_note','=',d['title'])])
                if len(title_account)==1:
                    d['title_code'] = title_account[0]['code']
                    d['company_id'] = title_account[0].analytic_account_id.company_id.id
                    d['analytic_account_id'] = title_account[0].analytic_account_id.id
                else:
                    message += ", title "+d['title']+" not/double in ad issues"
                    d['title_code'] = ""
                    d['company_id'] = ""
                    d['analytic_account_id'] = ""

                #register every page to facilitate manual adaption
                pages           = title_summary['paginas']  

                for page in pages :
                    d['page_nr']      = int(page['paginaNummer'])
                    _logger.debug("page : "+str(d['page_nr']))

                    d['page_type']=page.get('paginaType', 'n.a.') 
                    if d['page_type'] is None :
                        d['page_type']='n.a.'
                    
                    d['page_style']   = page.get('paginaStramien','n.a.')
                    if d['page_style'] is None :
                        d['page_style']='n.a.'


                    d['is_spread'] = page['isSpread']

                    #Geen overervig = 0
                    #Overerving van toepassing, pagina is bron = 1
                    #Overerving van toepassing, pagina is doel = 2
                    #Overerving van toepassing, pagina is wissel = 3
                    if page['overerving']== 3 :
                        d['is_inherited'] = True
                    else :
                        d['is_inherited'] = False 

                    d['ad_count']   = int(page['advertentieAantal'])
                    
                    if d['page_type']=="Advertentie" :
                        d['ad_page'] = 1 #+ self.conditional_ad(d['is_spread'], 1)
                        d['ed_page'] = 0
                    else :
                        d['ad_page'] = 0
                        d['ed_page'] = 1 #+ self.conditional_ad(d['is_spread'], 1)
                    
                    existing_recs = pubble_kpis.search([('title',     '=', d['title']     ),
                                                        ('issue_date','=', d['issue_date']),
                                                        ('page_nr',   '=', d['page_nr']   )
                                    ])
                    
                    if len(existing_recs)==0 :
                        pubble_kpis.create(d)
                    else :
                        d['issue_date']=existing_recs[0].issue_date
                        existing_recs[0].write(d)

            #leave testimonial with config info
            config.latest_issue   = most_recent
            config.latest_run     = datetime.date.today().strftime('%Y-%m-%d')+message
            config.latest_success = datetime.date.today()
            config.latest_status  = status
            config.latest_reason  = reason
            config.write({})
            return True
        else :
            config.latest_run     = datetime.date.today()
            config.latest_status  = status
            config.latest_reason  = reason
            config.write({})
            return False

		