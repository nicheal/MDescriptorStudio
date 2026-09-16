import createPlotlyComponent from "react-plotly.js/factory";
import * as Plotly from "plotly.js/lib/core";
import * as bar from "plotly.js/lib/bar";
import * as box from "plotly.js/lib/box";
import * as heatmap from "plotly.js/lib/heatmap";
import * as histogram from "plotly.js/lib/histogram";
import * as histogram2dcontour from "plotly.js/lib/histogram2dcontour";
import * as scattergl from "plotly.js/lib/scattergl";

// `lib/core` includes the default cartesian scatter trace; register the other
// trace modules used by Analysis and its result views explicitly.
Plotly.register([bar, box, heatmap, histogram, histogram2dcontour, scattergl]);

export default createPlotlyComponent(Plotly);
