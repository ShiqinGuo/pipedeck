import api from './zh/api';
import common from './zh/common';
import components from './zh/components';
import dashboard from './zh/dashboard';
import lib from './zh/lib';
import pipeline from './zh/pipeline';
import projects from './zh/projects';
import resources from './zh/resources';
import runDetail from './zh/runDetail';
import runs from './zh/runs';
import settings from './zh/settings';
import workspaceDetail from './zh/workspaceDetail';
import workspaces from './zh/workspaces';

export default {
  ...api,
  ...common,
  ...components,
  ...dashboard,
  ...lib,
  ...pipeline,
  ...projects,
  ...resources,
  ...runDetail,
  ...runs,
  ...settings,
  ...workspaceDetail,
  ...workspaces,
};
