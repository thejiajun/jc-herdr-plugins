import json
import unittest
from unittest.mock import patch
import switcher
import presentation
import context_data

class PickerTests(unittest.TestCase):
    def test_search_chinese_and_summary(self):
        agents=[dict(pane_id='w1:p1',workspace_label='创作者雷达',agent='codex',agent_status='blocked',_search='缓存刷新')]
        self.assertEqual(switcher.filtered(agents,'雷达 CODEX'),agents)
        self.assertEqual(switcher.filtered(agents,'等待确认'),agents)
        self.assertEqual(switcher.filtered(agents,'缓存'),agents)
        self.assertEqual(switcher.filtered(agents,'claude'),[])
    def test_width_and_control(self):
        self.assertEqual(presentation.clipped('中文abc',5),'中文a')
        self.assertEqual(presentation.clipped('a\x1bb',9),'ab')
        self.assertEqual(presentation.width(presentation.cell('测试中文',5)),5)
    def test_modified_keys(self):
        for key in ('\x1b\n','\x1b[106;7u','\x1b[27;7;106~'):
            self.assertEqual(switcher.decode(key),'next')
        for key in ('\x1b\x0b','\x1b[107;7u','\x1b[27;7;107~'):
            self.assertEqual(switcher.decode(key),'previous')
        self.assertIsNone(switcher.decode('\x1b[106;7:3u'))
        self.assertIsNone(switcher.decode('\x1b[57442;3:3u'))
        self.assertEqual(switcher.decode('\x1b'),'cancel')
        for seq,key in (('\x1b[A',switcher.curses.KEY_UP),('\x1bOB',switcher.curses.KEY_DOWN),('\x1b[1;2C',switcher.curses.KEY_RIGHT),('\x1b[D',switcher.curses.KEY_LEFT),('\x1b[57352u',switcher.curses.KEY_UP),('\x1b[5~',switcher.curses.KEY_PPAGE)):
            self.assertEqual(switcher.decode(seq),key)
        self.assertIsNone(switcher.decode('\x1b[999;7u'))
    def test_kitty_base_layout_key(self):
        # Russian ф reports US base 'a'; shifted И → 'B'; Ctrl+Alt+о (base j) still means next.
        self.assertEqual(switcher.decode('\x1b[1092::97u'),'a')
        self.assertEqual(switcher.decode('\x1b[1080:1048:98;2u'),'B')
        self.assertEqual(switcher.decode('\x1b[1086::106;7u'),'next')
        self.assertEqual(switcher.decode('\x1b[98u'),'b')
    def test_full_width_keys_normalize(self):
        self.assertEqual(switcher.normalize('ｂ'),'b')
        self.assertEqual(switcher.normalize('／'),'/')
        self.assertEqual(switcher.normalize('登'),'登')
        self.assertEqual(switcher.normalize('㎏'),'㎏')
        self.assertEqual(switcher.normalize(switcher.curses.KEY_UP),switcher.curses.KEY_UP)
    def test_status_filter_combines_with_query(self):
        agents=[dict(pane_id='1',agent_status='blocked',_search='jira'),dict(pane_id='2',agent_status='working',_search='jira'),dict(pane_id='3',agent_status='blocked',_search='')]
        self.assertEqual([a['pane_id'] for a in switcher.filtered(agents,'jira','blocked')],['1'])
        self.assertEqual([a['pane_id'] for a in switcher.filtered(agents,'','blocked')],['1','3'])
        self.assertEqual(len(switcher.filtered(agents,'jira')),2)
    @patch('switcher.context_data.load_cache',return_value={})
    @patch('switcher.cli')
    def test_sidebar_name_and_foreground_directory(self,cli,cache):
        cli.return_value={'snapshot':{'panes':[{'agent':'codex','pane_id':'w1:p1','workspace_id':'w1','label':'部署更新到3台机器','cwd':'/old','foreground_cwd':'/actual'}],'workspaces':[{'workspace_id':'w1','label':'测试'}]}}
        row=switcher.fetch_agents()[0]
        self.assertEqual(row['display_name'],'部署更新到3台机器')
        self.assertEqual(row['project_dir'],'/actual')

class ImmediatePool:
    def __init__(self,**kwargs): pass
    def submit(self,fn,*args,**kwargs):
        from concurrent.futures import Future
        future=Future()
        try: future.set_result(fn(*args,**kwargs))
        except Exception as exc: future.set_exception(exc)
        return future
    def shutdown(self,**kwargs): pass

class Window:
    def __init__(self,width): self.width=width; self.drawn=[]
    def getmaxyx(self): return (40,self.width)
    def addstr(self,y,x,text,attr):
        assert 0<=y<40 and 0<=x<self.width
        self.drawn.append(text)
    def timeout(self,*args): pass
    def keypad(self,*args): pass
    def erase(self): pass
    def refresh(self): pass
    def bkgd(self,*args): pass

class InteractionTests(unittest.TestCase):
    def run_ui(self,keys,width=46,statuses=('working',)*3):
        agents=[dict(pane_id=f'w{i}:p1',workspace_id=f'w{i}',workspace_label=f'项目{i}',display_name=f'部署任务{i}',project_dir='/actual/project',agent='codex',agent_status=statuses[i-1],focused=i==1) for i in (1,2,3)]
        agents[2]['_search']='jira 登录报错'
        win=Window(width)
        with patch('switcher.fetch_agents',return_value=agents),patch('switcher.read_details',return_value=('screen',{'request':'测试请求','reply':'测试回复'})),patch('switcher.cli') as cli,patch('switcher.keypress',side_effect=keys),patch('switcher.concurrent.futures.ThreadPoolExecutor',ImmediatePool),patch.multiple('switcher.curses',curs_set=lambda *_:None,set_escdelay=lambda *_:None,start_color=lambda:None,use_default_colors=lambda:None,init_pair=lambda *_:None,color_pair=lambda *_:0),patch.dict('os.environ',{'HERDR_SWITCHER_DIRECTION':'0'}),patch('switcher.presentation.draw',wraps=presentation.draw) as draw:
            result=switcher.ui(win)
            self.draws=draw.call_args_list
            return result,win,cli
    def test_narrow_wide_tables_focus(self):
        for width in (46,160):
            result,win,cli=self.run_ui([None,'next',None,'next',None,'previous',None,'\n'],width=width)
            self.assertEqual(result,'w2:p1')
            cli.assert_called_once_with('agent','get','w2:p1')
            self.assertTrue(any('AGENT' in s for s in win.drawn))
            self.assertTrue(any('最新对话' in s for s in win.drawn))
    def test_popup_stays_open_without_input(self):
        with self.assertRaises(StopIteration): self.run_ui([None,None,None])
    def test_jk_navigate(self):
        result,_,_=self.run_ui(['j','j','k','\n'])
        self.assertEqual(result,'w2:p1')
    def test_typing_starts_search_then_jk_are_letters(self):
        result,_,_=self.run_ui(['登','录','\n'])
        self.assertEqual(result,'w3:p1')
        result,_,_=self.run_ui(['/','j','i','r','a','\n'])
        self.assertEqual(result,'w3:p1')
    def test_filter_keys_select_status(self):
        statuses=('working','blocked','done')
        for keys,expected in ((['b','\n'],'w2:p1'),(['d','\n'],'w3:p1'),(['w','\n'],'w1:p1'),(['b','b','\n'],'w2:p1'),(['d','a','\n'],'w3:p1'),(['b','cancel','\n'],'w2:p1')):
            result,_,_=self.run_ui(keys,statuses=statuses)
            self.assertEqual(result,expected,keys)
        _,win,_=self.run_ui(['b','\n'],width=160,statuses=statuses)
        self.assertIn('[b 等待 1]',win.drawn)
        self.assertIn(' a 全部 3 ',win.drawn)
    def test_full_width_filter_and_navigation(self):
        result,_,_=self.run_ui(['ｂ','\n'],statuses=('working','blocked','done'))
        self.assertEqual(result,'w2:p1')
        result,_,_=self.run_ui(['ｊ','\n'])
        self.assertEqual(result,'w2:p1')
    def test_filter_letters_do_not_start_search(self):
        self.run_ui(['a','b','w','d','i','\n'],statuses=('idle','blocked','done'))
        self.assertTrue(all(c.args[4]=='' and not c.args[11] for c in self.draws))
        # Same letters type normally once in search mode.
        self.run_ui(['/','b','d','cancel','cancel'])
        self.assertEqual(self.draws[3].args[4],'bd')
    def test_filter_and_query_combine(self):
        statuses=('working','blocked','blocked')
        result,_,_=self.run_ui(['b','登','录','\n'],statuses=statuses)
        self.assertEqual(result,'w3:p1')
        self.assertEqual(len(self.draws[-1].args[1]),1)
    def test_escape_clears_search_then_closes(self):
        result,_,cli=self.run_ui(['登','cancel','k','\n'])
        self.assertEqual(result,'w2:p1')
        result,_,cli=self.run_ui(['cancel'])
        self.assertIsNone(result);cli.assert_not_called()

class ContextTests(unittest.TestCase):
    def test_stale_herdr_bin_path_falls_back_to_path(self):
        with patch.dict('os.environ', {'HERDR_BIN_PATH':'/missing/herdr'}), patch('context_data.shutil.which', return_value='/usr/local/bin/herdr'):
            self.assertEqual(context_data.herdr_bin(), '/usr/local/bin/herdr')
    def test_no_previous_reply_for_new_request(self):
        with patch('context_data.messages',return_value=([('user','旧请求'),('assistant','旧回复'),('user','新请求')],'native')):
            s=context_data.signals({})
        self.assertEqual(s['request'],'新请求');self.assertEqual(s['reply'],'')
    def test_ignore_injected_and_analysis(self):
        row={'type':'response_item','payload':{'type':'message','role':'assistant','channel':'analysis','content':[{'type':'output_text','text':'private'}]}}
        self.assertIsNone(context_data.parse_row(row,'codex'))
        self.assertFalse(context_data.usable('user','# AGENTS.md instructions\nignore me'))
    def test_session_identity_does_not_collide(self):
        a={'agent':'codex','pane_id':'w1:p1','agent_session':{'value':'a'}}
        b={**a,'agent_session':{'value':'b'}}
        self.assertNotEqual(context_data.identity(a),context_data.identity(b))
    def test_summary_validation(self):
        payload={'summaries':[dict(id='known',title='标题',request='请求',progress='报告',next='未提及'),dict(id='unknown',title='错误')]}
        self.assertEqual(list(context_data.validate(payload,{'known'})),['known'])


class SessionCwdTests(unittest.TestCase):
    def check(self,kind,rows,expected):
        import tempfile,pathlib
        with tempfile.NamedTemporaryFile('w',suffix='.jsonl',delete=False) as f:
            f.write('\n'.join(json.dumps(r) for r in rows))
        with patch('context_data.session_path',return_value=pathlib.Path(f.name)):
            self.assertEqual(context_data.session_cwd({'agent':kind,'agent_session':{'value':'x'}}),expected)
    def test_claude_uses_latest_row_cwd(self):
        self.check('claude',[{'cwd':'/launch'},{'cwd':'/moved'},{'type':'last-prompt'}],'/moved')
    def test_codex_uses_latest_turn_context(self):
        self.check('codex',[{'type':'session_meta','payload':{'cwd':'/launch'}},{'type':'turn_context','payload':{'cwd':'/moved'}},{'type':'response_item','payload':{}}],'/moved')
    def test_other_agents_fall_back(self):
        self.assertIsNone(context_data.session_cwd({'agent':'pi'}))


class ClaudeConfigDirTests(unittest.TestCase):
    """Sessions under a non-default CLAUDE_CONFIG_DIR (e.g. ~/.claude-personal) must still resolve."""
    def setUp(self):
        import tempfile,pathlib,os
        self.tmp=tempfile.TemporaryDirectory(); self.home=pathlib.Path(self.tmp.name)
        self.env=patch.dict('os.environ',{'HOME':str(self.home)}); self.env.start()
        os.environ.pop('CLAUDE_CONFIG_DIR',None)
        context_data._PATHS.clear()
    def tearDown(self):
        self.env.stop(); self.tmp.cleanup(); context_data._PATHS.clear()
    def transcript(self,config,sid,text,mtime,cwd='/work/app'):
        import os
        folder=self.home/config/'projects'/context_data.project_slug(cwd); folder.mkdir(parents=True,exist_ok=True)
        path=folder/f'{sid}.jsonl'
        path.write_text(json.dumps({'cwd':cwd,'message':{'role':'user','content':text}})+'\n')
        os.utime(path,(mtime,mtime))
        return path
    def test_bases_cover_every_config_dir(self):
        import os
        (self.home/'.claude/projects').mkdir(parents=True); (self.home/'.claude-personal/projects').mkdir(parents=True)
        (self.home/'.claude-code-ui').mkdir()
        custom=self.home/'elsewhere'; (custom/'projects').mkdir(parents=True)
        os.environ['CLAUDE_CONFIG_DIR']=str(custom)
        self.assertEqual({p.parent.name for p in context_data.claude_bases()},{'elsewhere','.claude','.claude-personal'})
    def test_known_session_id_found_in_personal_dir(self):
        path=self.transcript('.claude-personal','s1','你好',100)
        self.assertEqual(context_data.session_path({'agent':'claude','cwd':'/work/app','agent_session':{'value':'s1'}}),path)
    def test_missing_session_inferred_from_cwd(self):
        self.transcript('.claude','work-old',"旧",100)
        self.transcript('.claude-personal','claimed','别人的',300)
        newest=self.transcript('.claude-personal','mine','最新请求',200)
        agents=[{'agent':'claude','pane_id':'p1','cwd':'/work/app'},
                {'agent':'claude','pane_id':'p2','cwd':'/work/app','agent_session':{'value':'claimed'}},
                {'agent':'codex','pane_id':'p3','cwd':'/work/app'}]
        context_data.link_sessions(agents)
        self.assertEqual(agents[0]['agent_session']['value'],'mine')
        self.assertNotIn('agent_session',agents[2])
        self.assertEqual(context_data.session_path(agents[0]),newest)
        s=context_data.signals(agents[0])
        self.assertEqual(s['request'],'最新请求'); self.assertIn('按目录推断',s['source'])
    def test_two_unlinked_panes_get_different_sessions(self):
        self.transcript('.claude-personal','a',"一",100); self.transcript('.claude-personal','b',"二",200)
        agents=[{'agent':'claude','pane_id':f'p{i}','cwd':'/work/app'} for i in (1,2)]
        context_data.link_sessions(agents)
        self.assertEqual(sorted(a['agent_session']['value'] for a in agents),['a','b'])

class DetailLayoutTests(unittest.TestCase):
    def test_request_and_reply_values_align(self):
        lines=[t for t,_ in presentation.detail_lines({},{'request':'请求'*30,'reply':'回复'},{},'',40,'conversation')]
        you=next(t for t in lines if t.startswith('你：')); bot=next(t for t in lines if t.startswith('Agent：'))
        self.assertEqual(presentation.width(you[:you.index('请')]),7)
        self.assertEqual(presentation.width(bot[:bot.index('回')]),7)
        wrapped=lines[lines.index(you)+1]
        self.assertTrue(wrapped.startswith(' '*7) and wrapped[7]!=' ')

class JumpTests(unittest.TestCase):
    @patch('switcher.context_data.state_dir')
    @patch('switcher.cli')
    @patch('switcher.time.sleep')
    @patch('switcher.os.kill',side_effect=ProcessLookupError)
    def test_focus_runs_only_after_popup_exit(self,kill,sleep,cli,state):
        cli.side_effect=[{'agent':{'workspace_id':'w1','tab_id':'w1:t1'}},{},{},{'agent':{'focused':True}}]
        switcher.finish_jump(123,'w1:p1')
        self.assertEqual([c.args for c in cli.call_args_list],[('agent','get','w1:p1'),('workspace','focus','w1'),('tab','focus','w1:t1'),('agent','focus','w1:p1')])


class SortTests(unittest.TestCase):
    rows=[{'pane_id':'a1','project_dir':'/b','_active':5},
          {'pane_id':'b1','project_dir':'/a','_active':1},
          {'pane_id':'a2','project_dir':'/b','_active':9},
          {'pane_id':'b2','project_dir':'/a','_active':3}]
    def test_recent_newest_first(self):
        self.assertEqual([a['pane_id'] for a in switcher.order_agents(self.rows,'recent')],['a2','a1','b2','b1'])
    def test_folder_groups_then_recent(self):
        self.assertEqual([a['pane_id'] for a in switcher.order_agents(self.rows,'folder')],['b2','b1','a2','a1'])
