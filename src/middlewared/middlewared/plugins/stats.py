from middlewared.client import ejson as json
from middlewared.schema import Dict, Int, List, Str, accepts
from middlewared.service import Service
from middlewared.utils import Popen

import glob
import os
import re
import subprocess


RRD_PATH = '/var/db/collectd/rrd/localhost/'
RE_DSTYPE = re.compile(r'ds\[(\w+)\]\.type = "(\w+)"')
RE_STEP = re.compile(r'step = (\d+)')
RE_LAST_UPDATE = re.compile(r'last_update = (\d+)')


class StatsService(Service):

    @accepts()
    def get_sources(self):
        """
        Returns an object with all available sources tried with metric datasets.
        """
        sources = {}
        if not os.path.exists(RRD_PATH):
            return {}
        for i in glob.glob('{}/*/*.rrd'.format(RRD_PATH)):
            source, metric = i.replace(RRD_PATH, '').split('/', 1)
            if metric.endswith('.rrd'):
                metric = metric[:-4]
            if source not in sources:
                sources[source] = []
            sources[source].append(metric)
        return sources

    @accepts(Str('source'), Str('type'))
    async def get_dataset_info(self, source, _type):
        """
        Returns info about a given dataset from some source.
        """
        rrdfile = '{}/{}/{}.rrd'.format(RRD_PATH, source, _type)
        proc = await Popen(
            ['/usr/local/bin/rrdtool', 'info', rrdfile],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        data, err = await proc.communicate()
        if proc.returncode != 0:
            raise ValueError('rrdtool failed: {}'.format(err.decode()))
        data = data.decode()

        info = {
            'source': source,
            'type': _type,
            'datasets': {}
        }
        for dataset, _type in RE_DSTYPE.findall(data):
            info['datasets'][dataset] = {'type': _type}

        reg = RE_STEP.search(data)
        if reg:
            info['step'] = int(reg.group(1))
        reg = RE_LAST_UPDATE.search(data)
        if reg:
            info['last_update'] = int(reg.group(1))
        return info

    @accepts(
        List('stats-list', items=[
            Dict(
                'stats-data',
                Str('source'),
                Str('type'),
                Str('dataset'),
                Str('cf', default='AVERAGE'),
                additional_attrs=False,
            )
        ]),
        Dict(
            'stats-filter',
            Int('step', default=10),
            Str('start', default='now-1h'),
            Str('end', default='now'),
        ),
    )
    async def get_data(self, data_list, stats):
        """
        Get data points from rrd files.
        """

        defs = []
        names_pair = []
        for i, data in enumerate(data_list):
            names_pair.append([data['source'], data['type']])
            rrdfile = '{}/{}/{}.rrd'.format(RRD_PATH, data['source'], data['type'])
            defs.extend([
                'DEF:xxx{}={}:{}:{}'.format(i, rrdfile, data['dataset'], data['cf']),
                'XPORT:xxx{}:{}/{}'.format(i, data['source'], data['type']),
            ])
        proc = await Popen(
            [
                '/usr/local/bin/rrdtool', 'xport', '--json',
                '--start', stats['start'], '--end', stats['end'],
            ] + (['--step', str(stats['step'])] if stats.get('step') else []) + defs,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        data, err = await proc.communicate()
        if proc.returncode != 0:
            raise ValueError('rrdtool failed: {}'.format(err.decode()))
        data = json.loads(data.decode())

        # Custom about property
        data['about'] = 'Data for ' + ','.join(['/'.join(i) for i in names_pair])
        return data
