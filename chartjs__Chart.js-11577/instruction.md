# tick backdrop painted incorrectly when align==='inner'

### Expected behavior

The backfrop shall be rendered under the text of the tick label

<img width="83" alt="image" src="/testbed/assets/chartjs__Chart.js-11577/issue-01.png">


The scale's tick options:

```
.....
x2: {
        type: 'linear',
        position: 'top',
        ticks: {
          callback: function (val: number, index: number, ticks: Tick[]) {        
            return index === 0 || index === ticks.length - 1 ? 'FOO' : '0';
          },
          color: 'green',
          backdropColor: 'yellow',
          showLabelBackdrop: true,
          align: 'inner',
        },
      },
....
```

### Current behavior

When tick config "align" === 'inner' for a scale, the rightmost tick has its backdrop rendered outside, the text itself is correctly rendered inside 
<img width="44" alt="image" src="/testbed/assets/chartjs__Chart.js-11577/issue-02.png">


### Reproducible sample

/testbed/assets/chartjs__Chart.js-11577/reproduce.cjs

### Optional extra steps/info to reproduce

_No response_

### Possible solution

_No response_

### Context

_No response_

### chart.js version

4.4.0

### Browser name and version

Chrome 119

### Link to your project

_No response_

离线复现：运行 `node /testbed/assets/chartjs__Chart.js-11577/reproduce.cjs`，查看 `/testbed/.reproduction/` 中的输出。复现脚本使用当前源码，不包含修复或判分断言。

资源：`/testbed/assets/chartjs__Chart.js-11577/reproduce.cjs`

请在 `/testbed` 中修改代码，修复上述问题并保持既有行为。
