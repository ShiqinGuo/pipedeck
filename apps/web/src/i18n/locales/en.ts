import api from './en/api';
import integration from './en/integration';
import common from './en/common';
import components from './en/components';
import dashboard from './en/dashboard';
import lib from './en/lib';
import pipeline from './en/pipeline';
import projects from './en/projects';
import resources from './en/resources';
import runDetail from './en/runDetail';
import runs from './en/runs';
import settings from './en/settings';
import workspaceDetail from './en/workspaceDetail';
import workspaces from './en/workspaces';

export default {
  ...api,
  ...integration,
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
