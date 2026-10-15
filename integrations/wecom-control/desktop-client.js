window.__ModuleLoader__.load({
  id: 'weiyang-publicity-control',
  factory(require) {
    const { jsx, jsxs } = require('react/jsx-runtime');
    const { SettingsFormModel, settingsTextField, SettingsForm, SettingsValueField } = require('@deepseek-ai/dsh-client-ui-primitives');
    const ns = 'weiyang-publicity-control';
    const fields = ['enabled', 'projectRoot', 'configPath', 'python'];
    const labels = { enabled:'启用接续（true / false）',projectRoot:'项目绝对路径',configPath:'项目内私有配置路径',python:'Python 解释器' };
    const boolSpec = { field:'enabled', format:v => v === true ? 'true' : 'false',
      parse:text => text.trim() === 'true' ? {kind:'set',value:true} : text.trim() === 'false' ? {kind:'set',value:false} : undefined };
    function Page(props) {
      const state = props.useControlForm(s => s);
      if (props.view === 'summary') return '指定专用会话接续；先保持 false 并绑定 bridge_test。';
      return jsxs(SettingsForm, {
        labels:{unavailable:'配置尚未加载',readOnly:'只读',saveFailed:'保存失败，修改已保留',save:'保存配置',saving:'保存中'},
        state,onSave:props.save,onDiscard:props.discard,
        children:fields.map(field => jsx(SettingsValueField, {
          id:`weiyang-control-${field}`,label:labels[field],
          hint:field === 'enabled' ? '先保存项目路径并绑定私有测试会话；最后改为 true。业务权限由私有配置单独控制。' : undefined,
          overriddenLabel:'已覆盖',resetLabel:'恢复默认',invalidLabel:'请填写 true 或 false',
          disabled:!state.writable,...state[field],
          onEdit:text => props.edit(field,text),onReset:() => props.resetField(field),
        },field)),
      });
    }
    function apply(ctx) {
      const model = new SettingsFormModel(ctx.configForms.get(ns), [boolSpec,...fields.slice(1).map(settingsTextField)]);
      const store = model.bind(() => Object.assign(model.shell(),Object.fromEntries(fields.map(f=>[f,model.field(f)]))));
      ctx.effect(() => () => model.dispose());
      ctx.effect(() => ctx.configForms.whileServed([ns], () => ctx.slots.inject('plugins.row.config', () => ctx.slots.register({
        name:'plugins.row.config',key:`weiyang-publicity-control#${ns}`,
        inject:() => ({hooks:{controlForm:store},...model.actions()}),
      },Page))));
    }
    return { inject:['slots','configForms'], apply };
  },
});
