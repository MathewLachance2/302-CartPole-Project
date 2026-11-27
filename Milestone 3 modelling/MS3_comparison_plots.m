% Run this script to plot fitted lambda and its absolute percentage error.
% Change batchName to use another batch with a matching trial_errors.csv.
batchName = 'batch_20261126_165923_113';
comparisonFolder = fullfile(fileparts(mfilename('fullpath')), ...
    'Trial comparison', batchName);
runs = sortrows(readtable(fullfile(comparisonFolder,'trial_errors.csv')), ...
    {'bolts','travel_mm'});
assert(isequal(runs.bolts,repelem((1:3)',21)) && ...
    isequal(runs.travel_mm,repmat((50:70)',3,1)), ...
    'Expected one matched row per bolt count and travel distance.');

hardware = runs.lambda_meas_per_s;
simulation = runs.lambda_sim_fit_per_s;
assert(all(isfinite(hardware) & isfinite(simulation) & simulation ~= 0), ...
    'Each trial needs finite decay rates and a nonzero simulated reference.');
errorPct = 100 * abs(hardware - simulation) ./ abs(simulation);

% Bolt colours, markers, line widths and distance ticks follow MS3_batch_view.
colours = [.15 .55 .85; .94 .52 .14; .37 .70 .39];
markers = {'o','s','d'};
names = {'1 bolt (centre)','2 bolts (outer)','3 bolts (all)'};
[fitFig,fitAx] = comparisonAxes('Hardware and simulated decay rates', ...
    'Fitted decay rate \lambda (s^{-1})');
[errorFig,errorAx] = comparisonAxes('Absolute error in fitted decay rate', ...
    'Absolute error (% of simulated |\lambda|)');

for bolt = 1:3
    rows = runs.bolts == bolt;
    distance = runs.travel_mm(rows);
    style = {'Color',colours(bolt,:),'Marker',markers{bolt}, ...
        'MarkerFaceColor',colours(bolt,:),'MarkerSize',4,'LineWidth',1.4, ...
        'DisplayName',names{bolt}};
    plot(fitAx,distance,simulation(rows),'--','Color',colours(bolt,:), ...
        'LineWidth',1.4,'HandleVisibility','off');
    plot(fitAx,distance,hardware(rows),'-',style{:});
    plot(errorAx,distance,errorPct(rows),'-',style{:});
end
% A neutral legend sample identifies all three colour-matched reference lines.
plot(fitAx,NaN,NaN,'--','Color',[.25 .25 .25],'LineWidth',1.4, ...
    'DisplayName','MATLAB fit');
legend(fitAx,'Location','northoutside','Orientation','horizontal','FontSize',9);
legend(errorAx,'Location','northoutside','Orientation','horizontal','FontSize',9);
ylim(errorAx,[0 max(1,ceil(1.05*max(errorPct)))]);

exportgraphics(fitFig,fullfile(comparisonFolder,'fitted_lambda_matlab.png'), ...
    'Resolution',200);
exportgraphics(errorFig,fullfile(comparisonFolder,'lambda_error_matlab.png'), ...
    'Resolution',200);

function [fig,ax] = comparisonAxes(plotTitle,yLabel)
    fig = figure('Name',plotTitle,'NumberTitle','off','Position',[100 100 800 450]);
    theme(fig,'light');
    ax = axes(fig);
    hold(ax,'on'); grid(ax,'on'); box(ax,'on');
    xlim(ax,[49 71]); xticks(ax,50:5:70);
    xlabel(ax,'Travel distance (mm)'); ylabel(ax,yLabel);
    title(ax,plotTitle);
end
